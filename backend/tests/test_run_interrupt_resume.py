"""Interrupted runs — killed mid-answer, then retried.

Why this file exists
────────────────────
``test_run_rejoin.py`` proves the *stream* outlives a client: a browser that
closes its tab does not stop the run, and a rejoin replays the log. That is a
different question from the one an operator actually asks during an incident:
**what happens if the process running the turn dies while the answer is half
written?**

The distinction matters because the two are not symmetric. A client disconnect
must change nothing (that is the whole point of the durable run). A killed run
must change a lot: it never produced the turn, so it must not be recorded as
having completed, it must not leave a partially-written answer posing as the
reply, and a retry must produce the answer exactly once. Every one of those is a
failure mode we have already shipped once:

* the run log made a run's lifetime the HTTP request's, so an interrupted turn
  produced no assistant message, no usage row and no cost row;
* a synthesizer answer could be delivered twice — one row, two identical
  ``text_delta`` frames — which is exactly what a careless retry looks like from
  the client's side.

Measured against the same durable store the executor uses in production, with
``asyncio.Task.cancel()`` standing in for a SIGKILL that Python can survive
(a hard kill cannot be asserted on from inside the process that died; a
cancellation exercises the same terminal-write path, which is where the bugs
live).

What is asserted, and why each one is load-bearing:

1. the run is ``failed`` with ``run cancelled`` and ``message_id`` unset — a
   cancelled turn is not a completed turn;
2. no ``done`` frame exists — ``done`` is the client's signal that an answer is
   finished, and a client that sees it renders a half sentence as final;
3. the run log's ``seq`` values are contiguous from 1 — a rejoin pages by
   ``seq > after_seq``, so a gap is a hole the client can never fill;
4. no chunk appears twice — the duplicate-delivery class, asserted at the log
   rather than at the API;
5. a retry after the kill yields the answer exactly once, and leaves the dead
   run's log untouched — retrying must not mutate history or stack a second
   copy of the same answer onto the conversation.
"""

import asyncio
from collections import Counter

import pytest
from backend.app.domain.run.service import RunService

# Fixtures are resolved by name from the module that owns them; importing the
# fixture functions is what makes them visible here.
from tests.test_run_rejoin import (  # noqa: F401  (fixtures, referenced by name)
    FakeGraph,
    _new_run,
    _run_executor,
    broker,
    executor_env,
    session_factory,
    store,
)


def _chunk(text: str):
    """A LangChain-shaped chunk, without importing langchain into this test."""

    class _Chunk:
        type = "AIMessageChunk"

        def __init__(self, content: str) -> None:
            self.content = content

    return _Chunk(text)


def _delta_events(*texts: str) -> list[dict]:
    return [
        {"event": "on_chat_model_stream", "data": {"chunk": _chunk(text)}}
        for text in texts
    ]


class _BlockingGraph:
    """Emits its frames, then waits forever — a run that is genuinely in flight.

    ``FakeGraph`` finishes, which is the one thing an interrupt test cannot use:
    cancelling a run that has already completed proves nothing. The gate is never
    set, so the only way out of this generator is cancellation.
    """

    def __init__(self, events: list[dict], gate: asyncio.Event) -> None:
        self.events = events
        self.gate = gate
        self.cancelled = False
        # Counted as they are handed to the executor, so the test can wait for
        # "the answer is half-written" without depending on when the writer
        # flushes a batch -- frames are only readable from the store after a
        # flush, and the flush happens on cancellation.
        self.emitted = 0

    async def astream_events(self, *args, **kwargs):
        for event in self.events:
            self.emitted += 1
            yield event
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            # Recorded so the assertions can prove the *executor* ended the run,
            # not that the graph was never really running.
            self.cancelled = True
            raise

    async def aget_state(self, config):  # noqa: D401 - test double
        return None


async def _wait_until(predicate, *, timeout: float = 5.0) -> None:
    """Poll a cheap predicate; fail loudly instead of hanging on a broken run."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never became true; the run never started")


async def _events(session_factory, run_id, user_id):
    async with session_factory() as session:
        return await RunService(session).events_after(run_id, 0)


async def _finished_run(session_factory, run):
    async with session_factory() as session:
        return await RunService(session).get_for_user(run.id, run.user_id)


async def _cancel_mid_answer(session_factory, broker, run, *, texts: tuple[str, ...]):
    """Start a run, wait until its first frames are durable, then cancel it."""
    gate = asyncio.Event()
    graph = _BlockingGraph(_delta_events(*texts), gate)
    task = asyncio.create_task(_run_executor(session_factory, broker, run=run, graph=graph))

    async def _frames_landed() -> bool:
        return graph.emitted >= len(texts)

    try:
        await _wait_until(_frames_landed)
    finally:
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return graph


async def test_a_killed_run_fails_without_ever_claiming_the_answer_is_done(
    session_factory, executor_env, broker
):
    run = await _new_run(session_factory)
    graph = await _cancel_mid_answer(
        session_factory, broker, run, texts=("Hello ", "there")
    )

    finished = await _finished_run(session_factory, run)
    events = await _events(session_factory, run.id, run.user_id)
    kinds = [e.payload["type"] for e in events]

    assert graph.cancelled, "the graph should have been interrupted while waiting"
    assert finished.status == "failed"
    assert finished.error == "run cancelled"
    # A cancelled turn produced no answer, so it must not name a message as one.
    assert finished.message_id is None
    assert "done" not in kinds, "a cancelled run must never signal a finished answer"
    # What the user had on screen is still replayable: the frames that were
    # emitted before the kill are durable.
    assert kinds[:2] == ["text_delta", "text_delta"]

    # A cancelled turn is not a stored turn: an incomplete reply must not become
    # the conversation's answer for the next one to build on.
    assert executor_env["messages"] == []


async def test_a_killed_run_log_stays_contiguous_and_duplicate_free(
    session_factory, executor_env, broker
):
    """Rejoin pages by ``seq > after_seq``: a gap is unrecoverable, a repeat is a
    second answer. Both are asserted on the log, where the client sees them."""
    run = await _new_run(session_factory)
    await _cancel_mid_answer(session_factory, broker, run, texts=("Hello ", "there"))

    events = await _events(session_factory, run.id, run.user_id)

    assert [e.seq for e in events] == list(range(1, len(events) + 1))

    texts = [e.payload["content"] for e in events if e.payload["type"] == "text_delta"]
    assert set(texts) == {"Hello ", "there"}
    assert all(count == 1 for count in Counter(texts).values()), texts


async def test_retrying_after_a_kill_answers_once_and_leaves_the_dead_run_alone(
    session_factory, executor_env, broker
):
    """The retry is the dangerous moment: it re-asks a question the failed run may
    already have half-answered. The conversation must end up with one answer, and
    the dead run's record must stay exactly as the kill left it."""
    conversation_id = None
    run = await _new_run(session_factory)
    conversation_id = run.conversation_id
    await _cancel_mid_answer(session_factory, broker, run, texts=("Hello ", "there"))

    dead_events = await _events(session_factory, run.id, run.user_id)

    retry = await _new_run(session_factory, conversation_id=conversation_id)
    await _run_executor(
        session_factory,
        broker,
        run=retry,
        graph=FakeGraph(_delta_events("Hello ", "there")),
    )

    finished = await _finished_run(session_factory, retry)
    events = await _events(session_factory, retry.id, retry.user_id)
    kinds = [e.payload["type"] for e in events]

    assert finished.status == "completed"
    assert kinds[-1] == "done"
    # Exactly one answer, assembled from each chunk once.
    assert finished.message_id is not None
    assert events[-1].payload["content"] == "Hello there"
    texts = [e.payload["content"] for e in events if e.payload["type"] == "text_delta"]
    assert texts == ["Hello ", "there"], f"chunks were repeated: {texts}"
    assert [m["content"] for m in executor_env["messages"]] == ["Hello there"]

    # The failed run is history, not scratch space: a retry must not append to it.
    assert await _events(session_factory, run.id, run.user_id) == dead_events
    still_dead = await _finished_run(session_factory, run)
    assert still_dead.status == "failed"
