"""The executor keeps answer/usage/cost correctness and its timing spans intact.

Enqueueing the outbox rows is a fast insert *after* the persistence commit, not
a replacement for it. This file pins the ordering (persist -> commit -> enqueue
-> done), the existing spans/events, and the fail-open behaviour when the outbox
table is not available yet.
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
from backend.app.services.outbox.service import (  # noqa: E402
    TASK_CACHE_INVALIDATION,
    TASK_WEBHOOK_DISPATCH,
)
from backend.app.services.runs import executor as ex  # noqa: E402
from backend.app.services.runs.log import RunBroker  # noqa: E402
from backend.tests.fakes import FakeRunSession, FakeRunStore  # noqa: E402

ANSWER = "The sanitized answer"
CONFIG = {"configurable": {"thread_id": "t"}}


class _SpanRecorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    @asynccontextmanager
    async def __call__(self, name: str, attrs: dict | None = None):
        entry = {"name": name, "attrs": dict(attrs or {})}
        self.calls.append(entry)
        try:
            yield entry["attrs"]
        except Exception as exc:  # noqa: BLE001 - mirrors trace_span
            entry["error"] = type(exc).__name__
            raise


class _LogRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def info(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def warning(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def error(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def exception(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def names(self) -> list[str]:
        return [event for event, _ in self.events]

    def fields(self, name: str) -> list[dict]:
        return [fields for event, fields in self.events if event == name]


class _Graph:
    async def astream_events(self, **kwargs):
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content=ANSWER)},
        }

    async def aget_state(self, config):
        return SimpleNamespace(
            values={"answer_model": "gpt-x"}, next=(), tasks=[], created_at="now"
        )


class _OrderingSession(FakeRunSession):
    """Records the first commit that follows the assistant row insert."""

    def __init__(self, store: FakeRunStore, order: list[str]) -> None:
        super().__init__(store)
        self.order = order

    async def commit(self) -> None:
        await super().commit()
        if "message" in self.order and "persist_commit" not in self.order:
            self.order.append("persist_commit")


def _install_collaborators(monkeypatch, order: list[str], spans, logs):
    class _ConversationService:
        def __init__(self, session) -> None:
            pass

        async def add_message(self, **kwargs):
            order.append("message")
            return SimpleNamespace(id=uuid4())

    class _UsageService:
        def __init__(self, session) -> None:
            pass

        async def log_usage(self, payload):
            order.append("usage")

    class _CostTracking:
        def calculate_cost(self, *args) -> float:
            return 0.0

        async def record_cost_log(self, **kwargs):
            order.append("cost")

    class _Quality:
        def evaluate_response_quality(self, *args) -> dict:
            return {"score": 1.0}

    monkeypatch.setattr(ex, "trace_span", spans)
    monkeypatch.setattr(ex, "logger", logs)
    monkeypatch.setattr(ex, "ConversationService", _ConversationService)
    monkeypatch.setattr(ex, "UsageService", _UsageService)
    monkeypatch.setattr(ex, "cost_tracking_service", _CostTracking())
    monkeypatch.setattr(ex, "quality_service", _Quality())
    monkeypatch.setattr(ex.ai_client, "count_tokens", lambda *a, **k: 1)


def _install_outbox_recorder(monkeypatch, order: list[str], calls: list[dict], *, fail=False):
    async def _enqueue(session, **kwargs):
        calls.append(kwargs)
        order.append("enqueue")
        if fail:
            raise RuntimeError("post_answer_tasks does not exist yet")

    monkeypatch.setattr(ex, "enqueue_post_answer_tasks", _enqueue)

    def _inline(*args, **kwargs):
        order.append("inline_dispatch")

    monkeypatch.setattr(ex.semantic_response_cache, "invalidate", _inline)
    monkeypatch.setattr(ex, "_spawn_webhook_dispatch", _inline)


def _install_writer_recorder(monkeypatch, order: list[str], frames: list[dict] | None = None):
    original = ex.RunWriter

    class _RecordingWriter(original):
        async def emit(self, frame):
            if frames is not None:
                frames.append(frame)
            if frame.get("type") == "done":
                order.append("done")
            return await super().emit(frame)

    monkeypatch.setattr(ex, "RunWriter", _RecordingWriter)


async def _execute(store, order: list[str]):
    def session_factory():
        return _OrderingSession(store, order)

    async with session_factory() as session:
        run = await RunService(session).start_run(
            conversation_id=uuid4(), user_id=uuid4(), thread_id="t", mode="normal"
        )
    await ex.execute_run(
        run_id=run.id,
        graph=_Graph(),
        config=CONFIG,
        graph_input={},
        conversation_id=run.conversation_id,
        user_id=run.user_id,
        mode="normal",
        user_messages=[{"content": "the question"}],
        session_factory=session_factory,
        broker=RunBroker(),
    )
    return run


async def test_outbox_enqueue_happens_after_the_persistence_commit_and_before_done(
    monkeypatch: pytest.MonkeyPatch,
):
    order: list[str] = []
    enqueues: list[dict] = []
    spans = _SpanRecorder()
    logs = _LogRecorder()
    _install_collaborators(monkeypatch, order, spans, logs)
    _install_outbox_recorder(monkeypatch, order, enqueues)
    _install_writer_recorder(monkeypatch, order)

    run = await _execute(FakeRunStore(), order)

    assert len(enqueues) == 2
    assert {call["task_types"][0] for call in enqueues} == {
        TASK_CACHE_INVALIDATION,
        TASK_WEBHOOK_DISPATCH,
    }
    for call in enqueues:
        assert call["run_id"] == run.id
        assert call["user_id"] == run.user_id
        assert call["message_id"] is not None
        assert call["content"] == ANSWER
    index = {name: order.index(name) for name in ("message", "usage", "cost", "persist_commit", "enqueue", "done")}
    assert index["message"] < index["usage"] < index["cost"] < index["persist_commit"]
    assert index["persist_commit"] < index["enqueue"] < index["done"]
    assert "inline_dispatch" not in order


async def test_all_existing_timing_spans_events_and_fields_survive_the_enqueue(
    monkeypatch: pytest.MonkeyPatch,
):
    order: list[str] = []
    spans = _SpanRecorder()
    logs = _LogRecorder()
    _install_collaborators(monkeypatch, order, spans, logs)
    _install_outbox_recorder(monkeypatch, order, [])
    frames: list[dict] = []
    _install_writer_recorder(monkeypatch, order, frames)

    run = await _execute(FakeRunStore(), order)

    by_name: dict[str, list[dict]] = {}
    for span in spans.calls:
        by_name.setdefault(str(span["name"]), []).append(span)
    expected = {
        "chat.post_run_frames",
        "chat.persist.assistant_message",
        "chat.persist.usage_log",
        "chat.persist.cost_log",
        "chat.persist.cache_invalidate",
        "chat.persist.webhook_enqueue",
    }
    assert expected <= by_name.keys()
    assert {span["attrs"].get("phase") for span in by_name["chat.post_run_frames"]} == {
        "read",
        "emit",
    }
    for span in spans.calls:
        if not str(span["name"]).startswith("chat."):
            continue
        assert set(span["attrs"]) <= {"run_id", "message_id", "phase"}
        assert span["attrs"]["run_id"] == str(run.id)
        assert "user_id" not in span["attrs"]
        assert ANSWER not in [str(value) for value in span["attrs"].values()]

    names = logs.names()
    for event in ("bookkeeping_enqueued", "bookkeeping_finished", "done_emitted"):
        assert names.count(event) == 1, names
    assert names.index("bookkeeping_enqueued") < names.index("bookkeeping_finished")
    assert names.index("bookkeeping_finished") < names.index("done_emitted")
    done_frames = [frame for frame in frames if frame.get("type") == "done"]
    assert len(done_frames) == 1
    assert "bookkeeping_ms" in done_frames[0]
    assert isinstance(done_frames[0]["bookkeeping_ms"], (int, float))


async def test_enqueue_failure_never_loses_the_persisted_answer(
    monkeypatch: pytest.MonkeyPatch,
):
    order: list[str] = []
    spans = _SpanRecorder()
    logs = _LogRecorder()
    _install_collaborators(monkeypatch, order, spans, logs)
    _install_outbox_recorder(monkeypatch, order, [], fail=True)
    _install_writer_recorder(monkeypatch, order)

    await _execute(FakeRunStore(), order)

    assert order.index("persist_commit") < order.index("enqueue") < order.index("done")
    assert ex.PERSIST_STATE["status"] == "ok"
    assert any(event == "outbox_enqueue_failed" for event in logs.names())
