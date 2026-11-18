"""Observability for the live chat run's bookkeeping tail.

The `done` frame used to report `latency_ms` measured *before* the durable
write, so the seconds spent inserting the assistant row, logging usage and
cost, invalidating the cache and enqueuing the webhook were invisible in the
frame the client and the APM view both read. This file pins the additive fix:
new spans/events with bounded attributes, plus an honest `bookkeeping_ms`
field, without moving any of the work those numbers describe.

All collaborators are recorders (not mocks of the code under test): the spans,
the structured events and the frames are the observable outputs asserted here.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.domain.run.service import RunService  # noqa: E402
from backend.app.services.runs import executor as ex  # noqa: E402
from backend.app.services.runs.log import RunBroker  # noqa: E402
from backend.tests.fakes import FakeRunSession, FakeRunStore  # noqa: E402

ANSWER = "The sanitized answer"
CONFIG = {"configurable": {"thread_id": "t"}}


class _SpanRecorder:
    """Stands in for ``trace_span`` and records the fields a span is given."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @asynccontextmanager
    async def __call__(self, name: str, attrs: dict | None = None):
        entry: dict[str, object] = {
            "name": name,
            "attrs": dict(attrs or {}),
            "error": None,
        }
        self.calls.append(entry)
        try:
            yield entry["attrs"]
        except Exception as exc:  # noqa: BLE001 - recorder mirrors trace_span
            entry["error"] = type(exc).__name__
            raise


class _LogRecorder:
    """Captures the executor's structured events, with their bounded fields."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def _record(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def info(self, event: str, **fields: object) -> None:
        self._record(event, **fields)

    def warning(self, event: str, **fields: object) -> None:
        self._record(event, **fields)

    def error(self, event: str, **fields: object) -> None:
        self._record(event, **fields)

    def exception(self, event: str, **fields: object) -> None:
        self._record(event, **fields)

    def names(self) -> list[str]:
        return [event for event, _ in self.events]

    def fields(self, name: str) -> list[dict]:
        return [fields for event, fields in self.events if event == name]


class _Graph:
    """A graph that streams one answer and exposes a post-run checkpoint."""

    def __init__(self, *, text: str = ANSWER, values: dict | None = None) -> None:
        self._text = text
        self._values = values

    async def astream_events(self, **kwargs):
        if self._text:
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content=self._text)},
            }

    async def aget_state(self, config):
        return SimpleNamespace(
            values=self._values,
            next=(),
            tasks=[],
            created_at="2026-01-01 00:00:00",
        )


class _ExplodingGraph(_Graph):
    async def astream_events(self, **kwargs):
        raise RuntimeError("retrieval exploded")
        yield  # pragma: no cover - makes this an async generator


@pytest.fixture
def store() -> FakeRunStore:
    return FakeRunStore()


@pytest.fixture
def session_factory(store: FakeRunStore):
    return lambda: FakeRunSession(store)


@pytest.fixture
def broker() -> RunBroker:
    return RunBroker()


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace every collaborator the tail touches with a recorder."""
    spans = _SpanRecorder()
    logs = _LogRecorder()
    observed: dict[str, list] = {
        "messages": [],
        "usage": [],
        "cost_logs": [],
        "cache_invalidations": [],
        "webhooks": [],
    }

    class _ConversationService:
        def __init__(self, session) -> None:
            pass

        async def add_message(self, **kwargs):
            observed["messages"].append(kwargs)
            return SimpleNamespace(id=uuid4())

    class _UsageService:
        def __init__(self, session) -> None:
            pass

        @property
        def _repo(self):
            return self

        async def log_usage(self, payload):
            observed["usage"].append(payload)

    class _CostTracking:
        def calculate_cost(self, *args) -> float:
            return 0.0

        async def record_cost_log(self, **kwargs):
            observed["cost_logs"].append(kwargs)

    class _Quality:
        def evaluate_response_quality(self, *args) -> dict:
            return {"score": 1.0}

    async def _invalidate(**kwargs):
        observed["cache_invalidations"].append(kwargs)

    def _enqueue(**kwargs):
        observed["webhooks"].append(kwargs)

    monkeypatch.setattr(ex, "trace_span", spans)
    monkeypatch.setattr(ex, "logger", logs)
    monkeypatch.setattr(ex, "ConversationService", _ConversationService)
    monkeypatch.setattr(ex, "UsageService", _UsageService)
    monkeypatch.setattr(ex, "cost_tracking_service", _CostTracking())
    monkeypatch.setattr(ex, "quality_service", _Quality())
    monkeypatch.setattr(ex.ai_client, "count_tokens", lambda *a, **k: 1)
    monkeypatch.setattr(ex.semantic_response_cache, "invalidate", _invalidate)
    monkeypatch.setattr(ex, "_spawn_webhook_dispatch", _enqueue)
    return {"spans": spans, "logs": logs, "observed": observed}


async def _new_run(session_factory):
    async with session_factory() as session:
        return await RunService(session).start_run(
            conversation_id=uuid4(), user_id=uuid4(), thread_id="t", mode="normal"
        )


async def _execute(session_factory, broker, graph, run, *, broker_override=None, **kwargs):
    await ex.execute_run(
        run_id=run.id,
        graph=graph,
        config=CONFIG,
        graph_input={},
        conversation_id=run.conversation_id,
        user_id=run.user_id,
        mode="normal",
        user_messages=[{"content": "the question"}],
        session_factory=session_factory,
        broker=broker_override or broker,
        **kwargs,
    )


async def _frames(session_factory, run_id):
    async with session_factory() as session:
        events = await RunService(session).events_after(run_id, 0)
    return [event.payload for event in events]


async def _status(session_factory, run_id):
    async with session_factory() as session:
        return await RunService(session).get_for_user(run_id, uuid4())


# ── the spans ────────────────────────────────────────────────────────────────


async def test_every_tail_stage_has_a_named_span_with_bounded_attributes(
    session_factory, broker, recorded
):
    """The gap in the live trace is split into stages an APM view can rank."""
    run = await _new_run(session_factory)
    graph = _Graph(values={"artifact_id": "doc-1", "answer_model": "gpt-x"})
    await _execute(session_factory, broker, graph, run)

    spans = recorded["spans"].calls
    by_name: dict[str, list[dict]] = {}
    for span in spans:
        by_name.setdefault(str(span["name"]), []).append(span)

    expected = {
        "chat.post_run_frames",
        "chat.persist.assistant_message",
        "chat.persist.usage_log",
        "chat.persist.cost_log",
        "chat.persist.cache_invalidate",
        "chat.persist.webhook_enqueue",
    }
    assert expected <= by_name.keys(), sorted(by_name)

    # The checkpoint read and the frame emission loop are separate, narrow
    # phases of the same named span rather than one span spanning the write.
    assert {span["attrs"].get("phase") for span in by_name["chat.post_run_frames"]} == {
        "read",
        "emit",
    }

    for span in spans:
        if not str(span["name"]).startswith("chat."):
            continue  # `agentic_stream` is pre-existing and keeps its own contract.
        attrs = span["attrs"]
        assert set(attrs) <= {"run_id", "message_id", "phase"}, attrs
        assert attrs.get("run_id") == str(run.id), attrs
        assert "user_id" not in attrs, attrs
        assert ANSWER not in [str(value) for value in attrs.values()], attrs
        assert all(isinstance(value, str) for value in attrs.values()), attrs

    for name in (
        "chat.persist.usage_log",
        "chat.persist.cost_log",
        "chat.persist.cache_invalidate",
        "chat.persist.webhook_enqueue",
    ):
        assert all("message_id" in span["attrs"] for span in by_name[name]), name


async def test_post_run_frame_emission_is_spanned_on_the_empty_answer_path_too(
    session_factory, broker, recorded
):
    """The failing turn still emits its post-run frames; that loop is measurable."""
    run = await _new_run(session_factory)
    await _execute(session_factory, broker, _Graph(text=""), run)

    emit_phases = [
        span
        for span in recorded["spans"].calls
        if span["name"] == "chat.post_run_frames" and span["attrs"].get("phase") == "emit"
    ]
    assert len(emit_phases) == 1
    assert emit_phases[0]["attrs"]["run_id"] == str(run.id)


# ── the events ───────────────────────────────────────────────────────────────


async def test_chat_lifecycle_events_are_logged_in_execution_order(
    session_factory, broker, recorded
):
    """The four named events mark the moments the live trace could not see."""
    run = await _new_run(session_factory)
    await _execute(session_factory, broker, _Graph(), run)

    names = recorded["logs"].names()
    for event in ("answer_emitted", "bookkeeping_enqueued", "bookkeeping_finished", "done_emitted"):
        assert names.count(event) == 1, names
    assert names.index("answer_emitted") < names.index("bookkeeping_enqueued")
    assert names.index("bookkeeping_enqueued") < names.index("bookkeeping_finished")
    assert names.index("bookkeeping_finished") < names.index("done_emitted")

    # Structured events carry identifiers, never the answer or the user id.
    for event in ("answer_emitted", "bookkeeping_enqueued", "bookkeeping_finished", "done_emitted"):
        for fields in recorded["logs"].fields(event):
            assert "user_id" not in fields, fields
            assert ANSWER not in [str(value) for value in fields.values()], fields
            assert fields.get("run_id") == str(run.id), fields


async def test_answer_emitted_fires_once_when_text_first_reaches_the_stream(
    session_factory, broker, recorded
):
    """Two text deltas are one answer; the marker is at the first one."""
    run = await _new_run(session_factory)

    class _TwoDeltas(_Graph):
        async def astream_events(self, **kwargs):
            for part in ("The sanitized ", "answer"):
                yield {
                    "event": "on_chat_model_stream",
                    "data": {"chunk": SimpleNamespace(content=part)},
                }

    await _execute(session_factory, broker, _TwoDeltas(), run)

    assert recorded["logs"].names().count("answer_emitted") == 1
    frames = await _frames(session_factory, run.id)
    assert [f["content"] for f in frames if f["type"] == "text_delta"] == [
        "The sanitized ",
        "answer",
    ]


# ── the timing field ─────────────────────────────────────────────────────────


async def test_done_keeps_latency_ms_and_adds_a_non_negative_bookkeeping_ms(
    session_factory, broker, recorded
):
    """Contract compatibility: the old field stays; the honest one is added."""
    run = await _new_run(session_factory)
    await _execute(session_factory, broker, _Graph(), run)

    done = [f for f in await _frames(session_factory, run.id) if f["type"] == "done"]
    assert len(done) == 1
    assert "latency_ms" in done[0]
    assert "bookkeeping_ms" in done[0]
    assert isinstance(done[0]["latency_ms"], (int, float))
    assert isinstance(done[0]["bookkeeping_ms"], (int, float))
    assert done[0]["bookkeeping_ms"] >= 0


async def test_a_failed_persist_reports_bookkeeping_timing_on_the_existing_failure_path(
    session_factory, broker, recorded, monkeypatch: pytest.MonkeyPatch
):
    """A raise inside the bookkeeping does not lose its duration or add a frame."""

    async def _boom(**kwargs):
        raise RuntimeError("persistence is down")

    monkeypatch.setattr(ex, "_persist_reply", _boom)
    run = await _new_run(session_factory)
    await _execute(session_factory, broker, _ExplodingGraph(), run)

    failure_fields = recorded["logs"].fields("partial_reply_persist_failed")
    assert len(failure_fields) == 1
    assert isinstance(failure_fields[0]["bookkeeping_ms"], (int, float))
    assert failure_fields[0]["bookkeeping_ms"] >= 0
    assert "done" not in [f["type"] for f in await _frames(session_factory, run.id)]


# ── the paths that must not change ───────────────────────────────────────────


async def test_an_empty_answer_still_fails_without_a_done_frame(
    session_factory, broker, recorded
):
    """No answer means no persistence and no bookkeeping events."""
    run = await _new_run(session_factory)
    await _execute(session_factory, broker, _Graph(text=""), run)

    frames = await _frames(session_factory, run.id)
    assert "error" in [f["type"] for f in frames]
    assert "done" not in [f["type"] for f in frames]
    assert "bookkeeping_enqueued" not in recorded["logs"].names()
    assert not recorded["observed"]["messages"]


async def test_a_hitl_parked_run_still_completes_with_a_timed_done_frame(
    session_factory, broker, recorded
):
    """No answer text yet is legitimate when a pause frame is present."""
    run = await _new_run(session_factory)
    graph = _Graph(
        text="",
        values={
            "answer_model": "gpt-x",
            "artifact_id": "doc-1",
        },
    )

    class _Parked(_Graph):
        async def aget_state(self, config):
            snapshot = await super().aget_state(config)
            snapshot.next = ["approval"]
            return snapshot

    parked = _Parked(text="", values=graph._values)
    await _execute(session_factory, broker, parked, run)

    frames = await _frames(session_factory, run.id)
    assert any(f["type"] == "hitl_request" for f in frames), frames
    done = [f for f in frames if f["type"] == "done"]
    assert len(done) == 1
    assert done[0]["bookkeeping_ms"] >= 0
    assert "answer_emitted" not in recorded["logs"].names()
    assert "bookkeeping_enqueued" in recorded["logs"].names()


async def test_terminate_still_flushes_then_marks_status_then_publishes_end(
    session_factory, broker, recorded, monkeypatch: pytest.MonkeyPatch
):
    """The new spans sit outside `_terminate`; its load-bearing order is intact."""
    order: list[str] = []

    class _RecordingWriter(ex.RunWriter):
        async def flush(self):
            order.append("flush")
            return await super().flush()

    class _RecordingBroker(RunBroker):
        def publish_end(self, run_id):
            order.append("publish_end")
            return super().publish_end(run_id)

    original_finish = RunService.finish

    async def _finish(self, run_id, *args, **kwargs):
        order.append("status")
        return await original_finish(self, run_id, *args, **kwargs)

    monkeypatch.setattr(ex, "RunWriter", _RecordingWriter)
    monkeypatch.setattr(RunService, "finish", _finish)

    run = await _new_run(session_factory)
    await _execute(session_factory, broker, _Graph(), run, broker_override=_RecordingBroker())

    assert "flush" in order[:-2], order
    assert order[-2:] == ["status", "publish_end"], order
