"""
Run durability and rejoin — the tests for the fix recorded in AGENTS.md §9.25.

The defect under test: the SSE endpoint ran the graph inside its own
``StreamingResponse`` generator, so a run's lifetime was the HTTP request's. A
refresh closed the generator, the event loop hit its ``is_disconnected`` break,
and the rest of the answer was never produced — along with the assistant
message, the usage row and the cost row, because those were written *after* that
break. There was also no run id, no event log, and no endpoint to replay them,
so "resume after refresh" could not honestly be built on the client side.

Grouped by what has to be true. Each group is written so that reverting the
corresponding part of the fix makes it fail:

  A. The repository issues the queries we think it does — asserted on the
     compiled statement including the comparison *operator*, because flipping
     ``>`` to ``>=`` or ``eq`` to ``ne`` changes neither the parameters nor the
     string form (AGENTS.md §9.14).
  B. The writer's batching contract: live before durable, both flush triggers,
     and a bounded retry when the database is broken.
  C. The tailer's two rules: contiguous-only live delivery, and termination from
     ``status`` rather than from a stream going quiet.
  D. The executor's frame sequence is unchanged by the refactor — the refactor
     moved *who writes the log*, not what a client sees.
  E. The HTTP surface, including the ownership check on rejoin.
  F. The migration matches the models, so autogenerate reports no drift.

The durable store is faked (``FakeRunStore``) because SQLAlchemy's ability to
filter is not what is under test; group A is where the query itself is verified.
"""
import asyncio
import json
import operator
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend.app.domain.run.models import AgentRun, RunEvent
from backend.app.domain.run.repository import RunRepository
from backend.app.domain.run.service import RunService
from backend.app.services import run_executor
from backend.app.services.run_executor import execute_run, iter_graph_frames
from backend.app.services.run_log import (
    SUBSCRIBER_QUEUE_SIZE,
    RunBroker,
    RunWriter,
    tail_run,
)

from tests.fakes import (
    FakeResult,
    FakeRunSession,
    FakeRunStore,
    RecordingRunSession,
    _eval_clause,
)

# ── The durable store is faked ───────────────────────────────────────────────
# FakeRunStore / FakeRunSession live in tests/fakes.py alongside every other
# fake in this repo (AGENTS.md: "Fakes live in the single module"), because two
# test files need them: this one, and test_batch_a_wiring.py, whose /stream tests
# have to stop writing to the real database now that the endpoint opens a session
# for the run log. A second copy would be a second answer to "what does
# RunRepository actually issue", and the two would drift.

# The repo root, for the cross-repo checks (the migration's SQL, and that the BFF
# forwards the same header name). Resolved from this file rather than assumed to
# be the CWD, because AGENTS.md is explicit that pytest must be run from
# `backend/` and a relative path would be the first thing to break under it.
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def store() -> FakeRunStore:
    return FakeRunStore()


@pytest.fixture
def session_factory(store: FakeRunStore):
    return lambda: FakeRunSession(store)


@pytest.fixture
def broker() -> RunBroker:
    return RunBroker()


def _always_fails(message: str):
    """A session factory that dies on call.

    Synchronous on purpose: the writer enters the factory's *result* as an async
    context manager, so an ``async def`` factory would fail somewhere else
    entirely and never reach its body — which would make a retry-count assertion
    vacuous.
    """

    def _factory():
        raise RuntimeError(message)

    return _factory


async def _new_run(session_factory, **kw) -> AgentRun:
    async with session_factory() as session:
        return await RunService(session).start_run(
            conversation_id=kw.pop("conversation_id", uuid4()),
            user_id=kw.pop("user_id", uuid4()),
            thread_id=kw.pop("thread_id", "t"),
            mode=kw.pop("mode", "normal"),
        )


async def _collect(gen: AsyncGenerator) -> list:
    return [item async for item in gen]


async def _await_listener(broker, run_id, timeout: float = 5.0) -> None:
    """Wait until a reader is attached to ``run_id``, or raise ``TimeoutError``.

    Deliberately bounded *and* deliberately sleeping. The unbounded form,
    ``while broker.listener_count(run_id) < 1: await asyncio.sleep(0)``, looks
    like patience and is a busy-wait: it burns a core, and the moment the
    condition stops being reachable it never returns. That is not a theoretical
    concern -- it is exactly what the ``is_terminal`` revert produces, because
    the reader then returns before it ever subscribes. Discovered by the C2
    revert harness, which spent six minutes on 381s of CPU and then reported the
    revert as INCONCLUSIVE: a hang is not a result, and a harness that can hang
    cannot tell "load-bearing" from "machine is slow". A 10ms poll keeps the
    wait as cheap as ``sleep(0)`` without the spin.
    """

    async def _poll() -> None:
        while broker.listener_count(run_id) < 1:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout=timeout)


def _predicates(statement) -> dict[str, tuple]:
    """``{column: (operator, bound value)}`` for every conjunct of a statement.

    Read from the AST of the expression rather than from ``str(statement)``: the
    string form of ``seq > 3`` and ``seq >= 3`` differ only by one character,
    which is exactly the kind of difference a reader skims past.
    """
    from sqlalchemy.sql.elements import BinaryExpression

    out: dict[str, tuple] = {}
    for clause in statement._where_criteria:
        assert isinstance(clause, BinaryExpression), f"unexpected clause {clause!r}"
        out[clause.left.key] = (clause.operator, clause.right.value)
    return out


# ══ Group A — the queries are the ones we think ══════════════════════════════


async def test_the_replay_cursor_is_strictly_greater_than_what_the_client_has():
    """``seq > after_seq``, not ``>=``.

    ``>=`` replays the frame the client already holds on every reconnect, and the
    duplicate is indistinguishable from a real one downstream. Neither mistake
    shows up in the bound parameters, so the operator is asserted on its own.
    """
    session = RecordingRunSession(FakeRunStore())
    await RunRepository(session).events_after(uuid4(), 7)
    predicates = _predicates(session.statements[0])

    assert predicates["run_id"][0] is operator.eq
    assert predicates["seq"][0] is operator.gt, "must be strictly greater, not >="
    assert predicates["seq"][1] == 7


async def test_replay_is_ordered_ascending_and_bounded():
    """Ascending because frames replay out of order otherwise; bounded so a
    pathological run cannot make one reconnect buffer an unbounded response."""
    session = RecordingRunSession(FakeRunStore())
    await RunRepository(session).events_after(uuid4(), 0)
    statement = session.statements[0]

    assert statement._limit == 500
    assert "DESC" not in str(statement).upper()


async def test_the_ownership_check_filters_user_id_in_the_same_statement():
    """The IDOR guard must not be fetch-then-compare.

    Fetch-then-compare answers differently for "not yours" and "does not exist",
    which confirms to a caller that a guessed run id is real. Both predicates
    belong in one statement, so a run is never read before ownership is known.
    """
    session = RecordingRunSession(FakeRunStore())
    intruder = uuid4()
    await RunRepository(session).get_run_for_user(uuid4(), intruder)
    predicates = _predicates(session.statements[0])

    assert set(predicates) == {"id", "user_id"}
    assert predicates["user_id"] == (operator.eq, intruder)


async def test_events_from_two_runs_never_share_rows(session_factory):
    """``add_events`` takes the run id as an argument, not as instance state.

    Two runs in flight at once is the normal case, so a "current run" attribute
    would cross-write them silently instead of raising.
    """
    run_a, run_b = uuid4(), uuid4()
    async with session_factory() as session:
        await RunService(session).append_events(run_a, [(1, {"type": "a"})])
        await RunService(session).append_events(run_b, [(1, {"type": "b"})])

    async with session_factory() as session:
        got_a = await RunService(session).events_after(run_a, 0)
        got_b = await RunService(session).events_after(run_b, 0)

    assert [e.payload["type"] for e in got_a] == ["a"]
    assert [e.payload["type"] for e in got_b] == ["b"]


async def test_the_latest_run_for_a_conversation_is_the_newest_one(session_factory, store):
    """Ordering by time, so "which run was I watching" has an answer."""
    conversation_id, user_id = uuid4(), uuid4()
    created = []
    for day in (3, 1, 2):
        run = await _new_run(session_factory, conversation_id=conversation_id, user_id=user_id)
        run.created_at = datetime(2026, 1, day)
        created.append(run)

    async with session_factory() as session:
        latest = await RunService(session).latest_for_conversation(conversation_id, user_id)
        other_users = await RunService(session).latest_for_conversation(
            conversation_id, uuid4()
        )

    assert latest.id == created[0].id, "day 3 is the newest despite being inserted first"
    assert other_users is None, "a conversation with no runs of the caller's is not someone else's"


# ══ Group B — the writer's batching contract ════════════════════════════════


async def test_a_frame_reaches_a_subscriber_before_any_row_is_written(session_factory, broker):
    """Live delivery must not wait on the database.

    With batching effectively disabled the frame is observable while
    ``persisted`` is zero. A writer that flushed *before* publishing would pass
    every other test in this file and make streaming as slow as the database.
    """
    run_id = uuid4()
    writer = RunWriter(
        run_id=run_id, broker=broker, session_factory=session_factory,
        flush_interval=3600.0, batch_size=100_000,
    )
    queue = broker.subscribe(run_id)

    await writer.emit({"type": "text_delta", "content": "hi"})

    assert queue.get_nowait() == (1, {"type": "text_delta", "content": "hi"})
    assert writer.persisted == 0


async def test_sequence_numbers_are_one_based_and_gap_free(session_factory, broker):
    """A reader treats "the next seq" as the next frame, so there can be no holes."""
    writer = RunWriter(run_id=uuid4(), broker=broker, session_factory=session_factory)
    seqs = [await writer.emit({"type": "text_delta", "content": str(i)}) for i in range(25)]
    assert seqs == list(range(1, 26))


async def test_the_batch_size_forces_a_flush(session_factory, broker):
    writer = RunWriter(
        run_id=uuid4(), broker=broker, session_factory=session_factory,
        flush_interval=3600.0, batch_size=4,
    )
    for i in range(3):
        await writer.emit({"type": "text_delta", "content": str(i)})
    assert writer.persisted == 0, "3 of 4 must stay buffered"

    await writer.emit({"type": "text_delta", "content": "3"})
    assert writer.persisted == 4, "the fourth frame must force the batch out"


async def test_the_interval_forces_a_flush(session_factory, broker):
    """Time, not only count, must flush — a slow answer is still a stream."""
    writer = RunWriter(
        run_id=uuid4(), broker=broker, session_factory=session_factory,
        flush_interval=0.05, batch_size=100_000,
    )
    await writer.emit({"type": "text_delta", "content": "a"})
    assert writer.persisted == 0

    await asyncio.sleep(0.08)
    await writer.emit({"type": "text_delta", "content": "b"})
    assert writer.persisted == 2


async def test_flushing_an_empty_buffer_does_not_touch_the_database(session_factory, broker, store):
    """``flush`` runs from the terminal path, which may have nothing buffered."""
    writer = RunWriter(run_id=uuid4(), broker=broker, session_factory=session_factory)
    await writer.flush()
    assert store.commits == 0


async def test_a_broken_database_is_retried_once_then_dropped_rather_than_forever(broker):
    """Two failures drop the batch; an unbounded retry buffer would be a memory
    leak with a delayed fuse that only manifests while the database is unhealthy
    — exactly when the run is already at risk."""
    attempts = {"n": 0}

    def exploding_session_factory():
        attempts["n"] += 1
        raise RuntimeError("database is on fire")

    writer = RunWriter(
        run_id=uuid4(), broker=broker, session_factory=exploding_session_factory,
        flush_interval=0.0, batch_size=10,
    )
    await writer.emit({"type": "text_delta", "content": "a"})

    assert attempts["n"] == 2, "exactly one retry: two attempts in total"
    assert writer.dropped == 1, "the batch must be dropped, not queued forever"


async def test_a_frame_still_reaches_subscribers_when_the_database_is_down(broker):
    """The live path must not depend on the durable path at all.

    Asserted on the subscriber rather than on the writer's internals: if this
    failed, every connected user would get nothing during a database blip even
    though the answer was being produced normally.
    """
    dead_session_factory = _always_fails("no database")

    run_id = uuid4()
    queue = broker.subscribe(run_id)
    writer = RunWriter(
        run_id=run_id, broker=broker, session_factory=dead_session_factory,
        flush_interval=0.0, batch_size=1,
    )
    await writer.emit({"type": "text_delta", "content": "still streamed"})

    seq, frame = queue.get_nowait()
    assert (seq, frame["content"]) == (1, "still streamed")


async def test_a_full_subscriber_queue_drops_the_frame_rather_than_stalling_the_run(broker):
    """A slow reader must not be able to apply backpressure to the run.

    Dropping is the right direction: the reader detects the missing sequence and
    re-reads the durable copy. Blocking would let one abandoned browser tab
    freeze an answer for everyone else on the run.
    """
    run_id = uuid4()
    queue = broker.subscribe(run_id)
    assert queue.maxsize == SUBSCRIBER_QUEUE_SIZE

    for i in range(SUBSCRIBER_QUEUE_SIZE):
        broker.publish(run_id, i + 1, {"type": "text_delta", "content": str(i)})
    # The run must keep going. This returns rather than blocking.
    broker.publish(run_id, SUBSCRIBER_QUEUE_SIZE + 1, {"type": "text_delta", "content": "after"})

    assert queue.qsize() == SUBSCRIBER_QUEUE_SIZE
    assert queue.get_nowait()[0] == 1, "the oldest frames survive; the overflow is the cost"


# ══ Group C — the tailer's two rules ════════════════════════════════════════


async def test_a_client_joining_a_finished_run_gets_the_whole_answer(session_factory, broker):
    """The §9.25 promise, stated as a test: a refresh loses nothing.

    The run is terminal and every frame is durable, so the reader replays the
    answer from the start and stops — instead of showing the truncated tail that
    used to be all a reconnecting client could get.
    """
    run = await _new_run(session_factory)
    frames = [
        {"type": "text_delta", "content": "Hello"},
        {"type": "text_delta", "content": ", world"},
        {"type": "done", "content": "Hello, world"},
    ]
    async with session_factory() as session:
        await RunRepository(session).add_events(
            run.id, [(i + 1, f) for i, f in enumerate(frames)]
        )
        await RunService(session).finish(run.id, status="completed", event_count=3)

    replayed = await _collect(
        tail_run(run.id, session_factory=session_factory, broker=broker)
    )
    assert replayed == frames


async def test_after_seq_resumes_without_duplicating_or_skipping(session_factory, broker):
    """A client holding frames 1-2 asks for the rest, not for all of them."""
    run = await _new_run(session_factory)
    frames = [{"type": "text_delta", "content": str(i)} for i in range(5)]
    async with session_factory() as session:
        await RunRepository(session).add_events(
            run.id, [(i + 1, f) for i, f in enumerate(frames)]
        )
        await RunService(session).finish(run.id, status="completed")

    resumed = await _collect(
        tail_run(run.id, after_seq=2, session_factory=session_factory, broker=broker)
    )
    assert [f["content"] for f in resumed] == ["2", "3", "4"]


async def test_a_dropped_live_frame_is_recovered_from_the_log_before_its_successors(
    session_factory, broker
):
    """Contiguous-only delivery, and what happens when a frame never arrives live.

    Frames 1-4 arrive live. Frames 5 and 6 are written durably but not published
    (the shape a full subscriber queue produces), and 7 arrives live. A reader
    that yielded 7 on sight would advance its cursor to 7 and skip 5 and 6 as
    already seen — the answer silently loses a chunk.
    """
    run = await _new_run(session_factory)
    writer = RunWriter(
        run_id=run.id, broker=broker, session_factory=session_factory,
        flush_interval=0.0, batch_size=1,
    )
    # The reader attaches *first*, so frames 1-4 arrive live while it is reading.
    #
    # On `listener_count`, not on a sleep. A sleep is a guess about how long the
    # reader takes to reach `broker.subscribe`, and under a loaded machine (the
    # full suite) it guessed wrong: the reader attached *after* `publish_end`,
    # so it never saw frame 7 live, replayed only the six durable rows, and
    # stopped — `['0'..'5']` instead of `['0'..'6']`. This test passed alone and
    # failed in the suite, which is the shape of a flaky test rather than of a
    # flaky reader, and it was only the ordering of the assertions that made the
    # cause visible.
    reader = tail_run(run.id, session_factory=session_factory, broker=broker, poll_seconds=0.05)
    task = asyncio.create_task(_collect(reader))
    await _await_listener(broker, run.id)

    for i in range(4):
        await writer.emit({"type": "text_delta", "content": str(i)})

    # Durable, but never published live — the shape a full subscriber queue
    # produces when frames are dropped on their way to this reader.
    async with session_factory() as session:
        await RunRepository(session).add_events(
            run.id,
            [
                (5, {"type": "text_delta", "content": "4"}),
                (6, {"type": "text_delta", "content": "5"}),
            ],
        )
    broker.publish(run.id, 7, {"type": "text_delta", "content": "6"})

    async with session_factory() as session:
        await RunService(session).finish(run.id, status="completed")
    broker.publish_end(run.id)

    delivered = await asyncio.wait_for(task, timeout=5.0)
    assert [f["content"] for f in delivered] == ["0", "1", "2", "3", "4", "5", "6"]


async def test_the_reader_replays_first_then_follows_live(session_factory, broker):
    """The normal rejoin-mid-run shape: catch up, then stream.

    Both frames come from one writer with immediate flushes, which is the real
    shape -- a run has exactly one writer, and it owns the numbering. Hand-
    inserting a durable row and then letting a fresh writer publish would create
    a duplicate sequence number, which is not a state the executor can produce.
    """
    run = await _new_run(session_factory)
    writer = RunWriter(
        run_id=run.id, broker=broker, session_factory=session_factory,
        flush_interval=0.0, batch_size=1,
    )
    await writer.emit({"type": "text_delta", "content": "past"})

    reader = tail_run(run.id, session_factory=session_factory, broker=broker, poll_seconds=0.05)
    first = await anext(reader)

    await writer.emit({"type": "text_delta", "content": "live"})
    second = await anext(reader)

    async with session_factory() as session:
        await RunService(session).finish(run.id, status="completed")
    broker.publish_end(run.id)
    rest = await asyncio.wait_for(_collect(reader), timeout=5.0)

    assert [first["content"], second["content"]] == ["past", "live"]
    assert rest == [], "the completed run ends the read rather than hanging"


async def test_a_quiet_stream_does_not_end_the_read_only_status_does(session_factory, broker):
    """A silent stream is indistinguishable from a dead client.

    If "no frames for a while" ended the read, every quiet run would look
    finished and the client would stop before the answer arrived — which is the
    failure this whole change exists to remove.
    """
    run = await _new_run(session_factory)
    reader = tail_run(run.id, session_factory=session_factory, broker=broker, poll_seconds=0.02)

    task = asyncio.create_task(_collect(reader))
    # Wait for the reader to actually be attached before claiming it is still
    # reading. With a bare sleep the assertion holds even when the reader has not
    # started yet -- the task is trivially not done -- so the test would pass
    # without ever having exercised the idle path it exists to check.
    await _await_listener(broker, run.id)
    assert not task.done(), "the run is still running, so the reader must still be reading"

    async with session_factory() as session:
        await RunService(session).finish(run.id, status="failed", error="graph exploded")

    assert await asyncio.wait_for(task, timeout=5.0) == []


async def test_the_final_frames_are_read_even_when_status_flipped_first(
    session_factory, broker
):
    """The ordering guarantee, read from the tailer's side.

    A run marked complete with its last frames still unflushed would make a
    reader stop at whatever it had. Asserting that the ``done`` frame — the last
    one, and the only one carrying the full reply — is included pins the
    flush-before-status rule from both ends.
    """
    run = await _new_run(session_factory)
    async with session_factory() as session:
        await RunRepository(session).add_events(
            run.id, [(1, {"type": "text_delta", "content": "hi"})]
        )
    # Terminal status first, tail written after: exactly the ordering bug.
    async with session_factory() as session:
        await RunService(session).finish(run.id, status="completed")
    async with session_factory() as session:
        await RunRepository(session).add_events(
            run.id, [(2, {"type": "done", "content": "hi"})]
        )

    replayed = await asyncio.wait_for(
        _collect(tail_run(run.id, session_factory=session_factory, broker=broker, poll_seconds=0.05)),
        timeout=5.0,
    )
    assert replayed[-1]["type"] == "done"


async def test_closing_the_reader_unsubscribes_it(session_factory, broker):
    """A reader that goes away must not leave a queue still receiving frames.

    The run is seeded with one durable frame so the reader has something to
    yield: a reader attached to a run that is still running with nothing to say
    correctly waits, and waiting forever is the behaviour under test in the
    neighbouring test, not here.
    """
    run = await _new_run(session_factory)
    assert broker.listener_count(run.id) == 0
    async with session_factory() as session:
        await RunRepository(session).add_events(run.id, [(1, {"type": "text_delta", "content": "x"})])

    reader = tail_run(run.id, session_factory=session_factory, broker=broker, poll_seconds=0.02)
    await anext(reader)
    assert broker.listener_count(run.id) == 1

    await reader.aclose()
    assert broker.listener_count(run.id) == 0


async def test_the_end_of_run_sentinel_alone_stops_a_reader(session_factory, broker):
    """The sentinel is the fast path; ``status`` is the authority behind it.

    The reader has to be subscribed *before* the signal is published. Publishing
    first would send it to a queue that does not exist yet, and the reader would
    correctly wait — so the test would pass for a reason that has nothing to do
    with the sentinel.
    """
    run = await _new_run(session_factory)
    reader = tail_run(run.id, session_factory=session_factory, broker=broker, poll_seconds=30.0)
    task = asyncio.create_task(_collect(reader))
    await asyncio.sleep(0)

    broker.publish_end(run.id)
    assert await asyncio.wait_for(task, timeout=5.0) == []


async def test_a_missing_run_row_ends_the_reader_rather_than_hanging_it(session_factory, broker):
    """A stored run id referring to a deleted run gets an empty stream, so the
    client clears its storage, instead of a request that never completes."""
    collected = await asyncio.wait_for(
        _collect(tail_run(uuid4(), session_factory=session_factory, broker=broker, poll_seconds=0.02)),
        timeout=5.0,
    )
    assert collected == []


# ══ Group D — the executor ══════════════════════════════════════════════════


class FakeGraph:
    """Emits a fixed list of LangGraph event dicts."""

    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.seen: list[dict] = []

    async def astream_events(self, input, config=None, version=None):
        self.seen.append({"input": input, "config": config, "version": version})
        for event in self.events:
            yield event

    async def aget_state(self, config):
        return None


def _msg(content: str):
    return SimpleNamespace(content=content)


# Transcribed from the pre-refactor endpoint
# (``git show HEAD~:backend/app/api/v1/conversations.py``, the on_tool_start /
# on_tool_end / on_chain_stream / on_custom_event branches). If the refactor
# changes what a client sees, the next assertion fails — which is the point: it
# must not change what a client sees.
GOLDEN_EVENTS = [
    {"event": "on_chain_start", "name": "bootstrap"},
    {"event": "on_chat_model_stream", "data": {"chunk": _msg("Hello")}},
    {"event": "on_chat_model_stream", "data": {"chunk": _msg(" there")}},
    {"event": "on_tool_start", "name": "search", "run_id": "r1", "data": {"input": {"q": "x"}}},
    {"event": "on_tool_end", "name": "search", "run_id": "r1", "data": {"output": "found"}},
    {"event": "on_custom_event", "name": "citation", "data": {"source": "s1"}},
    {"event": "on_custom_event", "name": "not_forwarded", "data": {"x": 1}},
    {"event": "on_chain_end", "name": "synthesizer", "data": {"output": {"messages": [_msg("Hello there")]}}},
]

CONFIG = {"configurable": {"thread_id": "t"}}

# Just the two token streams, named so the intent survives: a reply of "Hello
# there", the shape every assertion in the executor group is about. Slicing
# GOLDEN_EVENTS[:2] would have included the on_chain_start and one delta, and the
# mismatch reads as a bug in the accumulator rather than in the fixture.
TWO_DELTAS = GOLDEN_EVENTS[1:3]


async def test_the_frame_sequence_is_unchanged_by_the_refactor():
    """Every frame, in order, exactly as the old endpoint produced them.

    Two things in there are easy to get wrong in a rewrite. The ``on_chain_end``
    fallback must not re-emit the final message once tokens have already
    streamed — that bug shipped once and produced the answer three times. And an
    unrecognised custom event must not be forwarded.
    """
    frames = [
        frame
        async for frame, _ in iter_graph_frames(
            FakeGraph(GOLDEN_EVENTS), config=CONFIG, graph_input={}
        )
    ]

    assert [f["type"] for f in frames] == [
        "text_delta",            # "Hello"
        "text_delta",            # " there"
        "tool_call",
        "TOOL_CALL_START",
        "tool_result",
        "TOOL_CALL_COMPLETE",
        "citation",
        # "not_forwarded" is absent: only the six named custom events are relayed.
    ]
    assert frames[0] == {"type": "text_delta", "content": "Hello"}
    assert frames[2]["tool_call_id"] == "r1"
    assert frames[2]["tool_input"] == {"q": "x"}
    assert frames[4]["result"] == "found"
    assert frames[6] == {"type": "citation", "source": "s1"}


async def test_a_custom_text_delta_event_is_marked_as_text():
    """``is_text_delta`` decides whether the reply is accumulated, so getting it
    wrong loses the answer while still showing it on screen."""
    event = {"event": "on_custom_event", "name": "text_delta", "data": {"content": "typed"}}
    pairs = [
        pair
        async for pair in iter_graph_frames(FakeGraph([event]), config=CONFIG, graph_input={})
    ]
    assert pairs == [({"type": "text_delta", "content": "typed"}, True)]


async def test_tool_output_is_truncated_before_it_reaches_the_log():
    """The log replays this frame to every reconnecting client, for as long as
    the run row exists."""
    event = {"event": "on_tool_end", "name": "t", "run_id": "r", "data": {"output": "x" * 9000}}
    frames = [
        frame
        async for frame, _ in iter_graph_frames(FakeGraph([event]), config=CONFIG, graph_input={})
    ]
    assert frames[0]["result"] == "x" * 2000


async def test_the_non_streaming_fallback_still_emits_the_final_message():
    """When no token stream arrives (the ToT node), the answer must still appear."""
    pairs = [
        pair
        async for pair in iter_graph_frames(
            FakeGraph([GOLDEN_EVENTS[-1]]), config=CONFIG, graph_input={}
        )
    ]
    assert pairs == [({"type": "text_delta", "content": "Hello there"}, True)]


@pytest.fixture
def executor_env(monkeypatch, session_factory):
    """Replace the executor's collaborators with recorders.

    These are the *caller's* dependencies, so substituting them is the right kind
    of fake; the persistence path is asserted through the calls they receive
    rather than through a mocked repository (AGENTS.md §9.14).
    """
    recorded: dict = {"messages": [], "usage": [], "costs": []}

    class _ConvSvc:
        def __init__(self, session):
            pass

        async def add_message(self, **kw):
            recorded["messages"].append(kw)
            return SimpleNamespace(id=uuid4())

    class _UsageSvc:
        def __init__(self, session):
            pass

        @property
        def _repo(self):
            return self

        async def log_usage(self, payload):
            recorded["usage"].append(payload)

    class _CostTracking:
        def calculate_cost(self, model, prompt_tokens, completion_tokens):
            return 0.0

        async def record_cost_log(self, **kw):
            recorded["costs"].append(kw)

    class _Quality:
        def evaluate_response_quality(self, *args):
            return {"score": 1.0}

    class _Tokens:
        @staticmethod
        def count_tokens(text, model):
            return 1

    @asynccontextmanager
    async def _span(name, attrs):
        yield

    monkeypatch.setattr(run_executor, "ConversationService", _ConvSvc)
    monkeypatch.setattr(run_executor, "UsageService", _UsageSvc)
    monkeypatch.setattr(run_executor, "cost_tracking_service", _CostTracking())
    monkeypatch.setattr(run_executor, "quality_service", _Quality())
    monkeypatch.setattr(run_executor, "ai_client", _Tokens)
    monkeypatch.setattr(run_executor, "trace_span", _span)
    return recorded


async def _run_executor(session_factory, broker, *, run, graph, user_messages=None, **kw):
    await execute_run(
        run_id=run.id,
        graph=graph,
        config=CONFIG,
        graph_input={},
        conversation_id=run.conversation_id,
        user_id=run.user_id,
        mode="normal",
        user_messages=user_messages or [],
        session_factory=session_factory,
        broker=broker,
        **kw,
    )


async def test_the_run_completes_and_persists_the_reply_with_nobody_attached(
    session_factory, executor_env, broker
):
    """The point of the whole change, as a test.

    Nothing is subscribed to this run — the closest a unit test gets to a browser
    that closed the tab. The reply must still be written, because that is exactly
    what the old design lost whenever a client disconnected.
    """
    run = await _new_run(session_factory)
    await _run_executor(
        session_factory, broker, run=run, graph=FakeGraph(TWO_DELTAS)
    )

    assert [m["content"] for m in executor_env["messages"]] == ["Hello there"]
    assert broker.listener_count(run.id) == 0


async def test_a_finished_run_is_terminal_only_after_every_frame_is_durable(
    session_factory, executor_env, broker
):
    """The ordering that stops a reconnect ending mid-answer.

    A reader decides the run is over from ``status``. If the status flipped
    before the final batch was committed, it would stop reading while the tail of
    the answer was still buffered — and the client would be short precisely the
    frames that had not been committed.
    """
    run = await _new_run(session_factory)
    await _run_executor(
        session_factory, broker, run=run, graph=FakeGraph(TWO_DELTAS)
    )

    async with session_factory() as session:
        finished = await RunService(session).get_for_user(run.id, run.user_id)
        events = await RunService(session).events_after(run.id, 0)

    assert finished.status == "completed"
    # Two text deltas plus the terminal `done`: nothing emitted is left behind.
    assert [e.payload["type"] for e in events] == ["text_delta", "text_delta", "done"]
    assert events[-1].payload["content"] == "Hello there"
    assert finished.event_count == len(events)
    assert finished.message_id is not None


async def test_a_graph_failure_becomes_an_error_frame_and_a_failed_run(
    session_factory, executor_env, broker
):
    class Exploding:
        async def astream_events(self, **kw):
            yield {"event": "on_chat_model_stream", "data": {"chunk": _msg("partial")}}
            raise RuntimeError("retrieval exploded")

        async def aget_state(self, config):
            return None

    run = await _new_run(session_factory)
    await _run_executor(session_factory, broker, run=run, graph=Exploding())

    async with session_factory() as session:
        finished = await RunService(session).get_for_user(run.id, run.user_id)
        events = await RunService(session).events_after(run.id, 0)

    assert finished.status == "failed"
    assert finished.error == "retrieval exploded"
    assert any(e.payload["type"] == "error" for e in events)
    # A failed run still persists what it produced: the partial answer the user
    # already had on screen must not be thrown away by a later failure.
    assert [m["content"] for m in executor_env["messages"]] == ["partial"]


async def test_an_empty_answer_persists_no_message(session_factory, executor_env, broker):
    """No text means no assistant row — the pre-existing guard, preserved."""
    run = await _new_run(session_factory)
    await _run_executor(session_factory, broker, run=run, graph=FakeGraph([]))

    async with session_factory() as session:
        finished = await RunService(session).get_for_user(run.id, run.user_id)
    assert finished.status == "completed"
    assert executor_env["messages"] == []


async def test_the_slot_is_released_even_when_the_graph_fails(session_factory, executor_env, broker):
    """The per-thread slot is what serialises runs on a thread; leaking it wedges
    the conversation permanently, so it is released on every exit path."""
    released: list[int] = []

    class Exploding:
        async def astream_events(self, **kw):
            raise RuntimeError("nope")
            yield  # pragma: no cover - unreachable, makes this an async generator

        async def aget_state(self, config):
            return None

    async def _release() -> None:
        released.append(1)

    run = await _new_run(session_factory)
    await _run_executor(session_factory, broker, run=run, graph=Exploding(), on_finish=_release)

    assert released == [1]


async def test_the_done_frame_carries_the_thread_and_the_latency(session_factory, executor_env, broker):
    """The terminal frame is what the BFF and the client both key off."""
    run = await _new_run(session_factory)
    await _run_executor(session_factory, broker, run=run, graph=FakeGraph(TWO_DELTAS))

    async with session_factory() as session:
        events = await RunService(session).events_after(run.id, 0)

    done = events[-1].payload
    assert done["type"] == "done"
    assert done["thread_id"] == "t"
    assert done["latency_ms"] >= 0


def test_the_encoder_matches_the_wire_format():
    """Replay and live share one encoder, so they cannot drift apart."""
    assert run_executor.encode_frame({"type": "done", "content": "x"}) == (
        'data: {"type": "done", "content": "x"}\n\n'
    )


async def test_an_unreadable_finished_state_does_not_lose_the_answer(
    session_factory, executor_env, broker
):
    """``aget_state`` failing must not swallow a complete reply.

    The pre-refactor code let that read raise into the stream's own handler, which
    emitted an error frame and no ``done`` — so a perfectly good answer was
    reported to the user as a failure.
    """

    class StateUnreadable(FakeGraph):
        async def aget_state(self, config):
            raise RuntimeError("checkpointer unavailable")

    run = await _new_run(session_factory)
    await _run_executor(
        session_factory, broker, run=run, graph=StateUnreadable(TWO_DELTAS)
    )

    async with session_factory() as session:
        finished = await RunService(session).get_for_user(run.id, run.user_id)
        events = await RunService(session).events_after(run.id, 0)

    assert finished.status == "completed"
    assert events[-1].payload["type"] == "done"


# The finished-state values that produce all three post-run frames at once. Every
# field is load-bearing and asserted below: a fixture that produced only `quality`
# would let the other two frames be lost without anything going red, which is
# exactly what happened here once (see the three tests below).
POST_RUN_VALUES = {
    "critique": {"verdict": "weak", "notes": "unsupported"},
    "revision_count": 2,
    "evidence_score": 0.42,
    "evidence_gate_passed": False,
    "artifact_id": "11111111-2222-3333-4444-555555555555",
    "artifact_title": "Deployment runbook",
    "artifact_version": 3,
    "artifact_created": False,
}


class StateWithResults(FakeGraph):
    """A graph whose finished checkpoint has a critique and an artifact.

    Every other executor test leaves ``aget_state`` returning ``None``, which is
    why the post-run frames were never exercised through the executor at all.
    """

    async def aget_state(self, config):
        return SimpleNamespace(
            values=POST_RUN_VALUES,
            next=(),
            tasks=[],
            created_at="2026-10-04T00:00:00Z",
        )


async def _post_run_frames_on_the_wire(session_factory, broker):
    """Run the executor over ``StateWithResults`` and return (run, logged frames)."""
    run = await _new_run(session_factory)
    await _run_executor(
        session_factory, broker, run=run, graph=StateWithResults(TWO_DELTAS)
    )
    async with session_factory() as session:
        events = await RunService(session).events_after(run.id, 0)
    return run, events


async def test_every_persisted_frame_is_an_object_and_not_a_pre_rendered_string(
    session_factory, executor_env, broker
):
    """The regression: the run log stores frames, so it must store objects.

    ``finished_run_events`` returns ready-to-yield SSE *strings*
    (``data: {...}\\n\\n``) because for its original caller -- a generator writing
    straight to the HTTP response -- that was exactly right. Splicing those
    strings into the list of frames the executor persists makes the run log hold a
    JSON string per frame, and ``encode_frame`` then serialises it a second time:

        data: "data: {\\"type\\": \\"quality\\"}\\n\\n"

    The client parses that, finds no ``type``, and drops the frame. So the
    critique, the quality score and the notification that a document was saved all
    disappear, with no error anywhere. It shipped green because
    ``test_batch_d3_run_events.py`` tests the pure function,
    ``test_batch_d3_artifact.py`` is a source-grep for the call, and every
    executor fixture left ``aget_state`` returning ``None`` so the frames were
    never built. Four layers, each green, and the user's artifact notification
    gone.
    """
    _, events = await _post_run_frames_on_the_wire(session_factory, broker)

    assert events, "the run persisted no frames at all; the fixture is wrong"
    for event in events:
        assert isinstance(event.payload, dict), (
            f"frame {event.seq} is a {type(event.payload).__name__}, not an object: "
            f"{event.payload!r}"
        )
        assert isinstance(event.payload.get("type"), str)


async def test_all_three_post_run_frames_survive_into_the_log(
    session_factory, executor_env, broker
):
    """Each of critique, quality and artifact must be *present*, not merely typed.

    A separate assertion from the one above on purpose: that one says "no frame is
    a string", which a log containing only ``text_delta`` and ``done`` also
    satisfies. This is the half that fails if the post-run frames are dropped,
    skipped, or emitted only when a field happens to be set.
    """
    _, events = await _post_run_frames_on_the_wire(session_factory, broker)
    by_type = {e.payload["type"]: e.payload for e in events}

    # Asserted against the fixture rather than a literal, so adding a field to
    # POST_RUN_VALUES cannot silently make this test pass on an empty frame.
    assert POST_RUN_VALUES["critique"]
    assert POST_RUN_VALUES["artifact_id"]

    assert by_type["critique"] == {
        "type": "critique",
        "critique": POST_RUN_VALUES["critique"],
        "revision_count": POST_RUN_VALUES["revision_count"],
    }
    assert by_type["quality"] == {
        "type": "quality",
        "evidence_score": POST_RUN_VALUES["evidence_score"],
        "evidence_gate_passed": POST_RUN_VALUES["evidence_gate_passed"],
    }
    assert by_type["artifact"] == {
        "type": "artifact",
        "artifact_id": POST_RUN_VALUES["artifact_id"],
        "title": POST_RUN_VALUES["artifact_title"],
        "version": POST_RUN_VALUES["artifact_version"],
        "created": False,
    }
    # The contract that puts artifact last: the client must have the final text
    # before it is asked to open a canvas over it, and the done frame last of all.
    order = [e.payload["type"] for e in events]
    assert order.index("artifact") > order.index("quality")
    assert order.index("quality") < order.index("done")


async def test_a_replayed_post_run_frame_is_byte_identical_to_the_old_wire_format(
    session_factory, executor_env, broker
):
    """The refactor changed *who writes the log*, not *what a client sees*.

    ``finished_run_events`` is used as the oracle precisely because it is the
    pre-refactor wire format, unchanged by this work -- so this compares the new
    path against the old definition rather than against a hand-copied string that
    would drift with it.
    """
    from backend.app.services.run_events import finished_run_events

    _, events = await _post_run_frames_on_the_wire(session_factory, broker)
    produced = [run_executor.encode_frame(e.payload) for e in events]
    old_wire = finished_run_events(POST_RUN_VALUES)

    for frame in old_wire:
        assert frame in produced, (
            f"the old wire format produced a frame the run log cannot reproduce: {frame!r}"
        )

    # And the two-frame shape the client would parse. Every line is checked the
    # way the BFF translator reads it: strip "data: ", JSON.parse, read .type.
    parsed = []
    for line in produced:
        assert line.startswith("data: ")
        assert line.endswith("\n\n")
        payload = json.loads(line[len("data: ") : -2])
        assert isinstance(payload, dict), f"not an object: {payload!r}"
        parsed.append(payload["type"])

    for expected in ("critique", "quality", "artifact"):
        assert expected in parsed, f"{expected} does not survive to the wire"


# ══ Group E — the HTTP surface ══════════════════════════════════════════════


def test_parse_last_event_id_falls_back_to_replaying_everything():
    """Unparseable input replays from the start.

    Duplicated frames are recoverable — the client already has them — whereas
    resuming from a wrong offset silently skips part of the answer.
    """
    from backend.app.api.v1.conversations import parse_last_event_id

    assert parse_last_event_id(None) == 0
    assert parse_last_event_id("") == 0
    assert parse_last_event_id("42") == 42
    assert parse_last_event_id("  7 ") == 7
    assert parse_last_event_id("not-a-number") == 0
    assert parse_last_event_id("-3") == 0, "a negative cursor must not read backwards"


def test_the_bff_forwards_the_same_run_id_header_name_the_backend_sets():
    """The header is one value across two repos, and nothing type-checks it.

    A rename on either side leaves a client that can never rejoin, with no failing
    test and no error anywhere — the same failure mode as an untranslated SSE
    frame. This is the test that makes the rename a red build.
    """
    from backend.app.api.v1.conversations import RUN_ID_HEADER

    bff = (REPO_ROOT / "frontend" / "src" / "app" / "api" / "chat" / "route.ts").read_text(
        encoding="utf-8"
    )
    assert RUN_ID_HEADER == "X-Nexus-Run-Id"
    assert RUN_ID_HEADER.lower() in bff.lower(), (
        f"{RUN_ID_HEADER} is set by the backend but not forwarded by the BFF"
    )


async def test_another_users_run_is_a_404(session_factory, monkeypatch):
    """The IDOR guard, end to end."""
    import backend.app.api.v1.conversations as conv_mod
    from backend.app.api.deps import get_current_user
    from backend.app.main import app
    from httpx import ASGITransport, AsyncClient

    victim = await _new_run(session_factory)
    intruder = SimpleNamespace(id=uuid4())

    async def _override():
        return intruder

    monkeypatch.setattr(conv_mod, "async_session_factory", session_factory)
    app.dependency_overrides[get_current_user] = _override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/conversations/{victim.conversation_id}/runs/{victim.id}/stream"
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 404
    assert "Run not found" in resp.text


async def test_a_run_cannot_be_rejoined_through_the_wrong_conversation(
    session_factory, monkeypatch
):
    """The path's conversation is checked too, not just ownership.

    Without it, a caller who owns conversation A could read the answer of a run
    they own on conversation B by quoting A's id in the path.
    """
    import backend.app.api.v1.conversations as conv_mod
    from backend.app.api.deps import get_current_user
    from backend.app.main import app
    from httpx import ASGITransport, AsyncClient

    me = uuid4()
    other_conversation = uuid4()
    run = await _new_run(session_factory, user_id=me, conversation_id=other_conversation)

    async def _override():
        return SimpleNamespace(id=me)

    monkeypatch.setattr(conv_mod, "async_session_factory", session_factory)
    app.dependency_overrides[get_current_user] = _override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/conversations/{uuid4()}/runs/{run.id}/stream")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 404


async def test_a_replay_is_byte_identical_to_a_live_stream(session_factory, monkeypatch):
    """One encoder, one log, one order — so a client needs no second code path
    for the reconnect case.

    This is the whole justification for reading both out of one place: if the
    formats could differ, every reconnect would be a chance to hit the truncated
    answer again.
    """
    import backend.app.api.v1.conversations as conv_mod

    run = await _new_run(session_factory)
    frames = [
        {"type": "text_delta", "content": "a"},
        {"type": "done", "content": "a"},
    ]
    async with session_factory() as session:
        await RunRepository(session).add_events(run.id, [(i + 1, f) for i, f in enumerate(frames)])
        await RunService(session).finish(run.id, status="completed")

    monkeypatch.setattr(conv_mod, "async_session_factory", session_factory)

    lines = [
        line
        async for line in conv_mod.frame_stream(run.id, after_seq=0)
    ]

    assert lines[-1] == "data: [DONE]\n\n"
    assert [json.loads(line[len("data: "):]) for line in lines[:-1]] == frames


async def test_the_stream_reader_turns_an_internal_failure_into_one_error_frame(
    session_factory, monkeypatch
):
    """A broken read must end the response, not hang the browser forever.

    And it must not fabricate a terminal ``done``: the client is told the stream
    broke, and the run's own status stays authoritative.
    """
    import backend.app.api.v1.conversations as conv_mod

    run = await _new_run(session_factory)

    def _exploding():
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(conv_mod, "async_session_factory", _exploding)

    lines = [line async for line in conv_mod.frame_stream(run.id, after_seq=0)]

    assert lines[-1] == "data: [DONE]\n\n"
    assert json.loads(lines[0][len("data: "):])["type"] == "error"


# ══ Group F — the migration matches the models ══════════════════════════════


def test_the_model_indexes_use_the_names_alembic_would_generate():
    """Autogenerate compares names, not intentions.

    ``index=True`` makes SQLModel name the index ``ix_<table>_<col>``. A migration
    that builds ``idx_<table>_<col>`` therefore reports a phantom diff on every
    future autogenerate, which is how real drift gets buried.
    """
    names = {index.name for index in RunEvent.__table__.indexes}
    assert "ix_run_events_run_id" in names
    assert "ix_run_events_seq" in names

    run_names = {index.name for index in AgentRun.__table__.indexes}
    assert {"ix_agent_runs_conversation_id", "ix_agent_runs_user_id", "ix_agent_runs_status"} <= run_names


def test_the_sequence_constraint_is_named_so_autogenerate_can_compare_it():
    """SQLAlchemy needs the name to match a constraint; an unnamed one is
    invisible to autogenerate and reads as drift forever."""
    unique = {
        constraint.name
        for constraint in RunEvent.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert unique == {"uq_run_events_run_id_seq"}


def test_the_columns_the_tailer_depends_on_exist():
    """Deleting any of these is a runtime AttributeError in production otherwise."""
    assert {"status", "conversation_id", "user_id", "message_id"} <= set(
        AgentRun.__table__.columns.keys()
    )
    assert {"run_id", "seq", "payload"} <= set(RunEvent.__table__.columns.keys())


def test_there_is_no_second_column_copying_the_payload_type():
    """``payload["type"]`` is the type.

    A parallel ``event_type`` column would be a second source of truth for the
    same value — the trap §9.18 documents about ``RAG_ANSWER_COVERAGE_DROP_BELOW``.
    Nothing needs to query by type here: replay is by ``seq`` and termination is
    ``AgentRun.status``.
    """
    assert "event_type" not in RunEvent.__table__.columns


def test_the_revision_extends_the_previous_head():
    """The graph must stay linear or ``upgrade head`` refuses to run at all —
    which is exactly how two independently-written revisions once left this
    repository with two heads and a dead first-boot command."""
    import ast

    path = next(Path("migrations/versions").glob("c2a1b2c3d4e5_*.py"))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = {
        node.target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        and node.target.id in ("revision", "down_revision")
    }
    assert values["revision"] == "c2a1b2c3d4e5"
    assert values["down_revision"] == "b1c2d3e4f5a6"


def test_the_migration_emits_the_same_table_and_index_names_it_declares():
    """The revision's SQL is checked against the models without a database.

    ``alembic upgrade --sql`` is the offline path: it renders the DDL without
    opening a connection, which is the only way to check a migration here without
    running it against a real database.
    """
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    dialect = postgresql.dialect()
    revision_sql = next(Path("migrations/versions").glob("c2a1b2c3d4e5_*.py")).read_text(
        encoding="utf-8"
    )

    for model in (AgentRun, RunEvent):
        table = model.__table__
        compiled = str(CreateTable(table).compile(dialect=dialect))
        assert f"CREATE TABLE {table.name}" in compiled or f"CREATE TABLE IF NOT EXISTS {table.name}" in compiled
        assert table.name in revision_sql
        for index in table.indexes:
            assert index.name in revision_sql, f"{index.name} missing from the revision"
