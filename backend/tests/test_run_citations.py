"""The run's evidence, from the checkpoint to the client and to the row.

## The defect this covers

Citations were dead at three independent points, each invisible on its own:

* the backend emitted no ``citation`` frame, because the only frame path that
  could carry one is ``on_custom_event`` -- an event langgraph never emits
  without being asked for ``stream_mode="custom"``, and even then it arrives as
  an ``on_chain_stream`` payload (AGENTS.md §9.23, measured on 1.2.11);
* the ``citations`` and ``tool_calls`` columns were never written by *any* code
  path, so the history loader had nothing to rebuild from;
* the client-side mappers were correct against those always-empty arrays, so they
  passed their own tests.

Every layer's tests were green and the feature did not exist. That is the shape
§2 of AGENTS.md describes one layer up, and it is why these tests assert across
the seam rather than inside one function.

The second half of the file covers the message row, which is the only durable
record of the same evidence -- so an answer reloaded tomorrow still shows what
it was based on.
"""
from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.domain.run.service import RunService  # noqa: E402
from backend.app.services.run_events import (  # noqa: E402
    MAX_CITATION_FRAMES,
    citation_payloads,
    finished_run_payloads,
)
from backend.app.services.run_executor import (  # noqa: E402
    _persist_reply,
    _run_citations,
    execute_run,
)
from backend.app.services.run_log import RunBroker  # noqa: E402

from tests.fakes import FakeRunSession, FakeRunStore  # noqa: E402


def _citation(filename: str = "notes.pdf", index: int = 0, score: float = 0.8) -> dict[str, Any]:
    return {
        "file_id": "5b2f0f3e-0000-4000-8000-000000000001",
        "filename": filename,
        "chunk_index": index,
        "score": score,
        "content_snippet": f"snippet {index}",
        "metadata": {"page": index},
    }


# ── the frames ──────────────────────────────────────────────────────────────


def test_a_finished_run_carries_its_citations_as_frames() -> None:
    frames = citation_payloads({"citations": [_citation()]})

    assert len(frames) == 1
    assert frames[0]["type"] == "citation"
    # The four fields the client renders. Everything else on a RAGCitation --
    # file_id, metadata -- is carried on the message row for the inspector to
    # fetch, not copied into a frame every reader of this run holds.
    assert set(frames[0]) == {
        "type",
        "filename",
        "chunk_index",
        "score",
        "content_snippet",
    }


def test_the_frame_is_json_serialisable_and_not_a_string() -> None:
    # The exact defect §9.27 records, in the opposite direction: a frame stored or
    # forwarded as a pre-rendered string is encoded a second time, parses to
    # something with no `type`, and is dropped by the translator with no error.
    frame = citation_payloads({"citations": [_citation()]})[0]
    assert isinstance(frame, dict)
    assert json.loads(json.dumps(frame))["type"] == "citation"


def test_the_citations_a_turn_used_are_ordered_as_the_state_holds_them() -> None:
    # The synthesizer's chosen set, in its own order: the answer refers to these
    # by number, so reordering them would point the numbering at the wrong chunk.
    frames = citation_payloads(
        {"citations": [_citation("a.pdf", 1), _citation("b.pdf", 2)]}
    )
    assert [f["filename"] for f in frames] == ["a.pdf", "b.pdf"]
    assert [f["chunk_index"] for f in frames] == [1, 2]


def test_a_run_without_retrieved_context_emits_no_citation_frames() -> None:
    # The common case by far. A `citation` frame with no content would render an
    # empty entry in the panel, which reads as "the source failed to load".
    assert citation_payloads({"citations": []}) == []
    assert citation_payloads({}) == []
    assert citation_payloads(None) == []
    assert citation_payloads("not a mapping") == []


def test_a_retrieval_wider_than_the_cap_is_bounded() -> None:
    # A broad research turn can retrieve hundreds of chunks, and every one would
    # be copied into a frame, into the run log, and onto the message row. The
    # first ones survive, not an arbitrary slice: the chunk the answer cites
    # first is the one a reader is most likely to check.
    frames = citation_payloads(
        {"citations": [_citation(index=i) for i in range(MAX_CITATION_FRAMES + 50)]}
    )
    assert len(frames) == MAX_CITATION_FRAMES
    assert frames[0]["chunk_index"] == 0


def test_a_malformed_citation_is_dropped_rather_than_forwarded() -> None:
    # A hand-built or older checkpoint can hold something that is not a mapping.
    # Forwarding it produces a frame the client cannot read, and an unreadable
    # frame is dropped there *silently* -- one fewer citation versus one fewer
    # answer.
    frames = citation_payloads({"citations": [_citation(), "oops", None]})
    assert len(frames) == 1
    assert frames[0]["type"] == "citation"


def test_citations_survive_beside_the_other_post_run_frames() -> None:
    # They are one set among several, and the module's other output must not be
    # disturbed by adding them.
    values = {
        "citations": [_citation()],
        "critique": "approve",
        "revision_count": 1,
        "evidence_score": 0.9,
        "response_text": "the answer",
    }
    post = finished_run_payloads(values) + citation_payloads(values)
    kinds = [f["type"] for f in post]
    assert "critique" in kinds, kinds
    assert "quality" in kinds, kinds
    assert "citation" in kinds, kinds


# ── the durable row ─────────────────────────────────────────────────────────


class _Row:
    """Stand-in for the persisted message; only its id is read back."""

    def __init__(self, row_id: Any) -> None:
        self.id = row_id


class _ConversationService:
    """Records what ``add_message`` was asked to write.

    Replacing the collaborator is what lets this assert the *values* rather than
    that a call happened -- the columns are the entire point of the test, and a
    fake that only counted calls would have passed before the fix.
    """

    WRITTEN: list[dict[str, Any]] = []

    def __init__(self, session: Any) -> None:
        self.session = session

    async def add_message(self, **kwargs: Any) -> _Row:
        type(self).WRITTEN.append(kwargs)
        return _Row(uuid4())


class _Session:
    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _UsageService:
    def __init__(self, session: Any) -> None:
        self._repo = self

    async def log_usage(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.fixture(autouse=True)
def _stub_the_row_writers(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Neutralise every collaborator that would reach outside this test.

    `_persist_reply` and `execute_run` between them write three rows (message,
    usage, cost log), evaluate a quality score, count tokens and open a tracing
    span. Only the message is under test, so the rest are replaced -- deliberately
    as *recorders* rather than mocks of the function under test, because the whole
    subject here is which arguments reached the writer (AGENTS.md §9.14).
    """
    import backend.app.services.run_executor as ex

    _ConversationService.WRITTEN = []
    monkeypatch.setattr(ex, "ConversationService", _ConversationService)
    monkeypatch.setattr(ex, "UsageService", _UsageService)
    monkeypatch.setattr(ex, "cost_tracking_service", _StubCost)
    monkeypatch.setattr(ex, "quality_service", _StubQuality)
    monkeypatch.setattr(ex.ai_client, "count_tokens", lambda *a, **k: 1)
    monkeypatch.setattr(ex, "trace_span", _null_span)
    return _ConversationService.WRITTEN


@asynccontextmanager
async def _null_span(name: Any = "", attrs: Any = None):
    """A tracing span that records nothing and opens nothing.

    The real one is a context manager over a network exporter; without this the
    executor tests hang or fail on a connection nobody in this repo controls.
    """
    yield


class _StubCost:
    @staticmethod
    def calculate_cost(*args: Any) -> float:
        return 0.0

    @staticmethod
    async def record_cost_log(**kwargs: Any) -> None:
        return None


class _StubQuality:
    @staticmethod
    def evaluate_response_quality(*args: Any) -> dict[str, Any]:
        return {}


def _reply_kwargs(**over: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "conversation_id": uuid4(),
        "user_id": uuid4(),
        "org_id": None,
        "user_messages": [{"content": "q"}],
        "emitted_text": "the answer",
        "latency_ms": 1.0,
        "parent_message_id": None,
        "run_id": uuid4(),
        "session_factory": _Session,
    }
    kwargs.update(over)
    return kwargs


def test_the_message_row_keeps_the_evidence_the_client_was_shown() -> None:
    # Before this, `citations` and `tool_calls` were left at their `[]` default on
    # every assistant message -- the columns had existed the whole time with
    # nothing writing them -- so a reloaded transcript showed the answer with an
    # empty citation panel and no record of the work behind it.
    citations = citation_payloads({"citations": [_citation()]})
    tool_calls = [
        {"tool_name": "web_search", "tool_input": {"q": "rag"}, "tool_call_id": "tc-1"}
    ]

    asyncio.run(
        _persist_reply(**_reply_kwargs(citations=citations, tool_calls=tool_calls))
    )

    written = _ConversationService.WRITTEN
    assert len(written) == 1, "no message row was written"
    assert written[0]["citations"] == citations
    assert written[0]["tool_calls"] == tool_calls


def test_a_run_with_no_evidence_writes_empty_lists_not_none() -> None:
    # `[]` and `None` are not the same column: the reader is a mapper that
    # iterates these arrays, and `None` is how it ends up iterating a null.
    asyncio.run(_persist_reply(**_reply_kwargs()))

    written = _ConversationService.WRITTEN
    assert written[0]["citations"] == []
    assert written[0]["tool_calls"] == []


# ── the read that feeds both ────────────────────────────────────────────────


class _Snapshot:
    def __init__(self, values: Any) -> None:
        self.values = values


class _Graph:
    def __init__(self, values: Any = None, fail: bool = False) -> None:
        self._values = values
        self._fail = fail

    async def aget_state(self, config: Any) -> Any:
        if self._fail:
            raise RuntimeError("checkpointer unreachable")
        return _Snapshot(self._values)


def test_the_failure_path_reads_citations_without_reading_a_verdict() -> None:
    # A run that died mid-answer has no critic verdict to report. The helper is
    # separate from the post-run frame builder precisely so this path cannot emit
    # a score for a partial reply.
    frames = asyncio.run(_run_citations(_Graph({"citations": [_citation()]}), {}))
    assert [f["type"] for f in frames] == ["citation"]


def test_a_checkpointer_that_cannot_be_read_loses_the_evidence_not_the_answer() -> None:
    # The caller is about to write the user's text to the database, and that write
    # matters more than the evidence attached to it.
    assert asyncio.run(_run_citations(_Graph(fail=True), {})) == []
    assert asyncio.run(_run_citations(_Graph(None), {})) == []
    assert asyncio.run(_run_citations(_Graph({"citations": []}), {})) == []


# ── the seam ────────────────────────────────────────────────────────────────
#
# Everything above this line calls a helper directly, which is the right way to
# test a helper and the wrong way to test a *chain*: the revert harness
# (`revert_c3.py`) reported C1, C4 and C5 not load-bearing, because reverting the
# executor's call sites left every test above green. The frame builder, the
# argument names and the collector each work in isolation, and the product still
# ships an answer with no citations -- which is the failure §2 of AGENTS.md
# describes, one layer down.
#
# So the rest of this file drives `execute_run` itself. The graph reports real
# state (this is the part every pre-existing executor fixture gets wrong -- they
# all return `None` from `aget_state`, so the post-run frames are never built and
# nothing downstream of them can be observed at all).


def _tool_event(name: str = "web_search", **kw: Any) -> dict[str, Any]:
    """The frame `iter_graph_frames` yields for a tool invocation.

    Shaped from the source rather than from memory: `on_tool_start` produces a
    `tool_call` frame carrying `tool_name`, `tool_input` and `tool_call_id`, and
    the same invocation also appears under the AG-UI `TOOL_CALL_START` alias.
    """
    return {
        "event": "on_tool_start",
        "name": name,
        "run_id": "r1",
        "data": {"input": {"q": "rag"}},
        **kw,
    }


def _text_event(content: str) -> dict[str, Any]:
    return {
        "event": "on_chat_model_stream",
        "data": {"chunk": SimpleNamespace(content=content)},
    }


class _StreamingGraph:
    """A graph whose stream raises part-way through, and whose state is real.

    The state matters as much as the stream: `aget_state` is where the citations
    live, and a fixture that returns `None` there makes every post-run frame
    vanish, which is how this chain shipped dead with a green suite.

    ``fail_after`` is the number of events yielded *before* the raise, rather than
    a predicate over the event. A predicate that matched the text event instead of
    following it produces a run that emitted nothing and then "failed" -- and
    `_persist_reply` deliberately returns `None` for empty text, so the test would
    have been measuring an early return while reading as a persistence failure.
    """

    def __init__(
        self, events: list[dict], values: Any, *, fail_after: int | None = None
    ) -> None:
        self.events = events
        self.values = values
        self.fail_after = fail_after
        self.state_reads = 0

    async def astream_events(self, input: Any, config: Any = None, version: Any = None):
        for i, event in enumerate(self.events):
            if self.fail_after is not None and i >= self.fail_after:
                raise RuntimeError("the model gave up")
            yield event
        if self.fail_after is not None:
            raise RuntimeError("the model gave up")

    async def aget_state(self, config: Any) -> Any:
        self.state_reads += 1
        return _Snapshot(self.values)


@pytest.fixture
def broker() -> RunBroker:
    return RunBroker()


@pytest.fixture
def store() -> FakeRunStore:
    return FakeRunStore()


@pytest.fixture
def session_factory(store: FakeRunStore):
    # Faked, shadowing the session-scoped real-database fixture in conftest.py:
    # this is the same reason test_run_rejoin.py and test_batch_a_wiring.py do it
    # (AGENTS.md §9.27) -- the endpoint opens sessions of its own and a real one
    # would write to whatever DATABASE_URL points at.
    return lambda: FakeRunSession(store)


async def _execute(session_factory, broker, run, graph):
    await execute_run(
        run_id=run.id,
        graph=graph,
        config={"configurable": {"thread_id": "t"}},
        graph_input={},
        conversation_id=run.conversation_id,
        user_id=run.user_id,
        mode="normal",
        user_messages=[{"content": "q"}],
        session_factory=session_factory,
        broker=broker,
    )


def _stored() -> dict[str, Any]:
    assert _ConversationService.WRITTEN, "no message row was written"
    return _ConversationService.WRITTEN[0]


async def test_the_executor_writes_the_runs_citations_onto_the_message_row(
    session_factory, broker, monkeypatch
):
    """The whole chain, end to end: state -> row.

    The three calls the harness reported as dead are exactly these three lines.
    Each of them works alone, and the product ships an answer with no evidence.
    """
    async with session_factory() as session:
        run = await RunService(session).start_run(
            conversation_id=uuid4(), user_id=uuid4(), thread_id="t", mode="normal"
        )

    graph = _StreamingGraph(
        [_text_event("the answer")], {"citations": [_citation()], "response_text": "the answer"}
    )
    await _execute(session_factory, broker, run, graph)

    stored = _stored()
    assert [c["filename"] for c in stored["citations"]] == ["notes.pdf"]
    # And the state was actually read -- without this, a graph returning `None`
    # would make the assertion above pass for the wrong reason on an empty list.
    assert graph.state_reads >= 1


async def test_the_executor_publishes_the_citations_to_the_run_log(
    session_factory, broker, monkeypatch
):
    """The client half of the same fact.

    Two consumers need the citations and they are not the same consumer: the
    message row is what a *reloaded* transcript reads, and the frames are what a
    *live* one renders. Wiring only the row leaves the citation panel empty until
    the next page load; wiring only the frames loses them entirely.
    """
    async with session_factory() as session:
        run = await RunService(session).start_run(
            conversation_id=uuid4(), user_id=uuid4(), thread_id="t", mode="normal"
        )

    graph = _StreamingGraph(
        [_text_event("the answer")], {"citations": [_citation()], "response_text": "the answer"}
    )
    await _execute(session_factory, broker, run, graph)

    # Read out of the durable store rather than off the broker: the broker is
    # in-process and empty once the run ends, whereas a rejoin reads the log.
    citations = [e for e in session_factory().store.events if e.payload.get("type") == "citation"]
    assert len(citations) == 1
    assert citations[0].payload["filename"] == "notes.pdf"
    # Before `done`, so a client that stops reading at the terminal frame still
    # has its evidence -- and so a rejoin's replay carries it.
    seqs = {e.payload.get("type"): e.seq for e in session_factory().store.events}
    assert seqs["citation"] < seqs["done"]


async def test_the_executor_stores_the_tool_calls_it_streamed(
    session_factory, broker, monkeypatch
):
    """Tool calls exist nowhere but here.

    The graph does not keep them in state -- `aget_state` returns no tool calls at
    all -- so the frame stream is the only place the information exists before the
    row is written. And the collector reads them *after* the stream has ended,
    which is why the argument has to be threaded through rather than read again.
    """
    async with session_factory() as session:
        run = await RunService(session).start_run(
            conversation_id=uuid4(), user_id=uuid4(), thread_id="t", mode="normal"
        )

    graph = _StreamingGraph(
        [_tool_event(), _text_event("the answer")],
        {"citations": [], "response_text": "the answer"},
    )
    await _execute(session_factory, broker, run, graph)

    stored = _stored()
    assert [c["tool_name"] for c in stored["tool_calls"]] == ["web_search"]
    assert stored["tool_calls"][0]["tool_input"] == {"q": "rag"}


async def test_a_run_that_dies_mid_answer_still_stores_what_it_had(
    session_factory, broker, monkeypatch
):
    """The failure path, which has no post-run frames at all.

    A run that dies cannot report a critique or a quality score -- there is no
    verdict for a partial reply -- but the sources it had already retrieved are
    real and were the basis of the text it *did* produce. The helper is separate
    from the post-run frame builder for exactly this reason, and this asserts the
    branch actually reaches it.
    """
    async with session_factory() as session:
        run = await RunService(session).start_run(
            conversation_id=uuid4(), user_id=uuid4(), thread_id="t", mode="normal"
        )

    graph = _StreamingGraph(
        [_text_event("partial"), _text_event(" more")],
        {"citations": [_citation("partial.pdf")]},
        fail_after=1,
    )
    await _execute(session_factory, broker, run, graph)

    stored = _stored()
    # The partial text first: losing the answer to keep the evidence would be a
    # far worse trade than the reverse.
    assert stored["content"] == "partial"
    assert [c["filename"] for c in stored["citations"]] == ["partial.pdf"]


async def test_a_run_that_produced_no_text_writes_no_message_row(
    session_factory, broker, monkeypatch
):
    """The other half of the failure path, and the one that is *not* a bug.

    `_persist_reply` returns `None` for an empty answer, and that is right: an
    assistant message with no content is an empty bubble in the transcript and a
    row the user cannot delete, for a turn that never said anything. The test
    exists so the previous test cannot be satisfied by *always* writing a row --
    which is the shape a "persist the partial reply" fix takes when it is applied
    as "write whatever we have".
    """
    async with session_factory() as session:
        run = await RunService(session).start_run(
            conversation_id=uuid4(), user_id=uuid4(), thread_id="t", mode="normal"
        )

    graph = _StreamingGraph([], {"citations": [_citation()]}, fail_after=0)
    await _execute(session_factory, broker, run, graph)

    assert _ConversationService.WRITTEN == []

