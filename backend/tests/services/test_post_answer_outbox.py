"""Durable post-answer outbox: enqueue, claim, retry and delivery.

The run's critical path must only pay for a fast, idempotent insert. Cache
invalidation and webhook delivery happen in the background worker, with bounded
attempts and a dead-letter terminal state. These tests pin those behaviours
against a real Postgres (the same dialect as the hosted database), because
``FOR UPDATE SKIP LOCKED`` and ``ON CONFLICT DO NOTHING`` are database semantics,
not something a fake can prove.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from backend.app.domain.conversation.models import Conversation
from backend.app.domain.run.models import AgentRun
from backend.app.services.outbox import handlers as outbox_handlers
from backend.app.services.outbox import service as outbox_service
from backend.app.services.outbox import worker as outbox_worker
from backend.app.services.outbox.models import PostAnswerTask
from sqlalchemy import delete, select


@pytest.mark.parametrize(
    ("attempts", "expected"),
    [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (5, 16.0)],
)
def test_retry_delay_doubles_on_each_failed_attempt(attempts, expected):
    """RED: the documented exponential backoff must actually grow."""
    assert outbox_service.retry_delay_seconds(attempts) == expected


def test_retry_delay_still_honours_the_cap_and_early_attempts():
    assert outbox_service.retry_delay_seconds(20) == 300.0
    assert outbox_service.retry_delay_seconds(3, base=2.0, cap=5.0) == 5.0
    assert outbox_service.retry_delay_seconds(0) == 1.0


class _LogRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def info(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def warning(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def error(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def names(self) -> list[str]:
        return [event for event, _ in self.events]

    def fields(self, name: str) -> list[dict]:
        return [fields for event, fields in self.events if event == name]


class _SpanRecorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, name: str, attrs: dict | None = None):
        @asynccontextmanager
        async def _record():
            entry = {"name": name, "attrs": dict(attrs or {})}
            self.calls.append(entry)
            yield entry["attrs"]

        return _record()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _make_run(session, user_id, conversation_id=None):
    conversation = Conversation(
        id=conversation_id or uuid4(), user_id=user_id, title="outbox test"
    )
    session.add(conversation)
    await session.flush()
    run = AgentRun(
        conversation_id=conversation.id,
        user_id=user_id,
        thread_id=str(uuid4()),
        mode="normal",
    )
    session.add(run)
    await session.flush()
    return run, conversation


def _task(
    run,
    *,
    task_type: str,
    key: str | None = None,
    payload: dict | None = None,
    attempts: int = 0,
    max_attempts: int = 5,
    available_at: datetime | None = None,
) -> PostAnswerTask:
    return PostAnswerTask(
        id=uuid4(),
        run_id=run.id,
        message_id=uuid4(),
        task_type=task_type,
        idempotency_key=key or f"manual-{uuid4()}",
        payload=payload or {},
        status="pending",
        attempts=attempts,
        max_attempts=max_attempts,
        available_at=available_at or (_now() - timedelta(seconds=1)),
        created_at=_now(),
    )


@pytest.fixture(autouse=True)
async def _isolated_outbox(db_session):
    """The shared test DB persists rows between tests; start each one empty."""
    await db_session.exec(delete(PostAnswerTask))
    await db_session.commit()


# ── enqueue ───────────────────────────────────────────────────────────────────


async def test_enqueue_is_idempotent_per_run_message_and_task_type(
    db_session, test_user, monkeypatch
):
    logs = _LogRecorder()
    monkeypatch.setattr(outbox_service, "logger", logs)
    run, conversation = await _make_run(db_session, test_user.id)
    message_id = uuid4()

    kwargs = dict(
        run_id=run.id,
        message_id=message_id,
        user_id=test_user.id,
        conversation_id=conversation.id,
        model="gpt-x",
        content="the answer",
    )
    first = await outbox_service.enqueue_post_answer_tasks(db_session, **kwargs)
    second = await outbox_service.enqueue_post_answer_tasks(db_session, **kwargs)

    rows = (
        await db_session.exec(
            select(PostAnswerTask).where(PostAnswerTask.run_id == run.id)
        )
    ).scalars().all()
    assert first == 2
    assert second == 0
    assert sorted(row.task_type for row in rows) == sorted(
        [outbox_service.TASK_CACHE_INVALIDATION, outbox_service.TASK_WEBHOOK_DISPATCH]
    )
    assert {row.idempotency_key for row in rows} == {
        outbox_service.idempotency_key(run.id, message_id, outbox_service.TASK_CACHE_INVALIDATION),
        outbox_service.idempotency_key(run.id, message_id, outbox_service.TASK_WEBHOOK_DISPATCH),
    }
    assert all(row.status == "pending" and row.attempts == 0 for row in rows)

    events = logs.fields("outbox.enqueue")
    assert len(events) == 2
    assert all(set(event) <= {"run_id", "message_id", "task_types"} for event in events)
    assert all(event["run_id"] == str(run.id) for event in events)
    assert all("the answer" not in str(event.values()) for event in events)
    assert all(str(test_user.id) not in str(event.values()) for event in events)


async def test_cache_invalidation_payload_carries_conversation_and_variant(db_session, test_user):
    """RED: invalidation must target the same scope as the cache write."""
    run, conversation = await _make_run(db_session, test_user.id)
    await outbox_service.enqueue_post_answer_tasks(
        db_session,
        run_id=run.id,
        message_id=uuid4(),
        user_id=test_user.id,
        conversation_id=conversation.id,
        model="gpt-x",
        content="the answer",
        prompt_variant="default:mem0-abc123",
        task_types=(outbox_service.TASK_CACHE_INVALIDATION,),
    )
    row = (
        await db_session.exec(
            select(PostAnswerTask).where(
                PostAnswerTask.run_id == run.id,
                PostAnswerTask.task_type == outbox_service.TASK_CACHE_INVALIDATION,
            )
        )
    ).scalars().one()

    assert row.payload == {
        "user_id": str(test_user.id),
        "model": "gpt-x",
        "conversation_id": str(conversation.id),
        "prompt_variant": "default:mem0-abc123",
    }


async def test_a_new_message_gets_its_own_tasks(db_session, test_user):
    run, conversation = await _make_run(db_session, test_user.id)
    common = dict(
        run_id=run.id,
        user_id=test_user.id,
        conversation_id=conversation.id,
        model="gpt-x",
        content="answer",
    )
    await outbox_service.enqueue_post_answer_tasks(db_session, message_id=uuid4(), **common)
    inserted = await outbox_service.enqueue_post_answer_tasks(
        db_session, message_id=uuid4(), **common
    )
    assert inserted == 2
    rows = (
        await db_session.exec(
            select(PostAnswerTask).where(PostAnswerTask.run_id == run.id)
        )
    ).scalars().all()
    assert len(rows) == 4


# ── claim ─────────────────────────────────────────────────────────────────────


async def test_claim_uses_skip_locked_so_two_workers_never_take_the_same_row(
    db_session, test_user, session_factory
):
    run, _conversation = await _make_run(db_session, test_user.id)
    db_session.add_all(
        [
            _task(run, task_type="cache", key="claim-a"),
            _task(run, task_type="webhook", key="claim-b"),
        ]
    )
    await db_session.commit()

    async with session_factory() as holder:
        held = (
            await holder.execute(outbox_service.claim_statement().limit(1))
        ).scalars().first()
        assert held is not None
        # The holder's transaction stays open, so its row remains locked while
        # the second session claims. SKIP LOCKED is what keeps this from blocking.
        async with session_factory() as other:
            claimed = await outbox_service.claim_next(other)
            assert claimed is not None
            assert claimed.id != held.id
            assert claimed.status == "running"
            assert claimed.attempts == 1
            assert claimed.locked_at is not None
        await holder.rollback()


async def test_claim_ignores_tasks_that_are_not_yet_available(
    db_session, test_user
):
    run, _conversation = await _make_run(db_session, test_user.id)
    db_session.add(
        _task(run, task_type="cache", key="not-yet", available_at=_now() + timedelta(hours=1))
    )
    await db_session.commit()

    assert await outbox_service.claim_next(db_session) is None


# ── retry / dead letter ───────────────────────────────────────────────────────


async def test_failure_requeues_with_backoff_then_dead_letters(
    db_session, test_user, monkeypatch
):
    logs = _LogRecorder()
    monkeypatch.setattr(outbox_service, "logger", logs)
    run, _conversation = await _make_run(db_session, test_user.id)
    db_session.add(_task(run, task_type="cache", key="retry", max_attempts=2))
    await db_session.commit()

    first = await outbox_service.claim_next(db_session)
    assert first is not None and first.status == "running" and first.attempts == 1
    before = _now()
    await outbox_service.mark_failed(db_session, first, "temporary failure")
    assert first.status == "pending"
    assert 0.9 <= (first.available_at - before).total_seconds() <= 2.0
    assert first.last_error == "temporary failure"
    assert logs.names().count("outbox.retry") == 1

    assert await outbox_service.claim_next(db_session) is None
    first.available_at = _now() - timedelta(seconds=1)
    db_session.add(first)
    await db_session.commit()

    second = await outbox_service.claim_next(db_session)
    assert second is not None and second.attempts == 2
    await outbox_service.mark_failed(db_session, second, "permanent failure")
    assert second.status == "dead_letter"
    assert second.finished_at is not None
    assert await outbox_service.claim_next(db_session) is None
    assert logs.names().count("outbox.retry") == 1
    assert logs.names().count("outbox.dead_letter") == 1
    for fields in logs.fields("outbox.retry") + logs.fields("outbox.dead_letter"):
        assert set(fields) <= {"task_id", "task_type", "run_id", "attempts", "next_delay_seconds"}
        assert "permanent failure" not in str(fields.values())


async def test_success_marks_row_succeeded_and_finishes(db_session, test_user):
    run, _conversation = await _make_run(db_session, test_user.id)
    db_session.add(_task(run, task_type="cache", key="success"))
    await db_session.commit()

    task = await outbox_service.claim_next(db_session)
    assert task is not None
    await outbox_service.mark_succeeded(db_session, task)
    assert task.status == "succeeded"
    assert task.finished_at is not None
    assert await outbox_service.claim_next(db_session) is None


# ── worker ────────────────────────────────────────────────────────────────────


async def test_worker_processes_both_task_types_with_registered_handlers(
    db_session, test_user, monkeypatch
):
    run, conversation = await _make_run(db_session, test_user.id)
    await outbox_service.enqueue_post_answer_tasks(
        db_session,
        run_id=run.id,
        message_id=uuid4(),
        user_id=test_user.id,
        conversation_id=conversation.id,
        model="gpt-x",
        content="answer",
    )
    called: list[tuple[str, dict]] = []

    async def _cache(session, payload):
        called.append(("cache", payload))

    async def _webhook(session, payload):
        called.append(("webhook", payload))

    monkeypatch.setattr(
        outbox_worker,
        "HANDLERS",
        {
            outbox_service.TASK_CACHE_INVALIDATION: _cache,
            outbox_service.TASK_WEBHOOK_DISPATCH: _webhook,
        },
    )
    logs = _LogRecorder()
    spans = _SpanRecorder()
    monkeypatch.setattr(outbox_worker, "logger", logs)
    monkeypatch.setattr(outbox_service, "logger", logs)
    monkeypatch.setattr(outbox_worker, "trace_span", spans)

    processed = 0
    while await outbox_worker.process_one(db_session):
        processed += 1
        assert processed <= 4  # fail loudly instead of looping forever

    assert processed == 2
    assert sorted(name for name, _ in called) == ["cache", "webhook"]
    rows = (
        await db_session.exec(
            select(PostAnswerTask).where(PostAnswerTask.run_id == run.id)
        )
    ).scalars().all()
    assert all(row.status == "succeeded" and row.finished_at for row in rows)
    assert logs.names().count("outbox.claim") == 2
    assert logs.names().count("outbox.deliver") == 2
    assert len(spans.calls) == 2
    assert all(span["name"] == "outbox.deliver" for span in spans.calls)
    for span in spans.calls:
        assert set(span["attrs"]) <= {"task_id", "task_type", "run_id"}
        assert span["attrs"]["run_id"] == str(run.id)


async def test_a_failing_handler_retries_and_eventually_dead_letters(
    db_session, test_user, monkeypatch
):
    logs = _LogRecorder()
    monkeypatch.setattr(outbox_service, "logger", logs)
    run, _conversation = await _make_run(db_session, test_user.id)
    db_session.add(_task(run, task_type="cache", key="failing", max_attempts=1))
    await db_session.commit()

    async def _boom(session, payload):
        raise RuntimeError("cache is down")

    monkeypatch.setattr(outbox_worker, "HANDLERS", {"cache": _boom})
    monkeypatch.setattr(outbox_worker, "logger", logs)
    monkeypatch.setattr(outbox_worker, "trace_span", _SpanRecorder())

    assert await outbox_worker.process_one(db_session) is True
    task = (await db_session.exec(select(PostAnswerTask))).scalars().one()
    assert task.status == "dead_letter"
    assert task.last_error == "cache is down"
    assert "outbox.dead_letter" in logs.names()


async def test_real_handlers_invalidate_cache_and_dispatch_webhook(
    db_session, test_user, monkeypatch
):
    run, conversation = await _make_run(db_session, test_user.id)
    message_id = uuid4()
    observed: dict[str, dict] = {}

    async def _invalidate(**kwargs):
        observed["cache"] = kwargs

    monkeypatch.setattr(outbox_handlers.semantic_response_cache, "invalidate", _invalidate)

    class _WebhookService:
        def __init__(self, session) -> None:
            pass

        async def dispatch_event(self, **kwargs):
            observed["webhook"] = kwargs
            return 1

    monkeypatch.setattr(outbox_handlers, "WebhookService", _WebhookService)

    await outbox_handlers.invalidate_semantic_cache(
        db_session,
        {
            "user_id": str(test_user.id),
            "model": "gpt-x",
            "conversation_id": str(conversation.id),
            "prompt_variant": "default:mem0-abc123",
        },
    )
    await outbox_handlers.dispatch_webhook(
        db_session,
        {
            "user_id": str(test_user.id),
            "conversation_id": str(conversation.id),
            "message_id": str(message_id),
            "model": "gpt-x",
            "content": "answer",
        },
    )

    assert observed["cache"] == {
        "user_id": str(test_user.id),
        "model": "gpt-x",
        "conversation_id": str(conversation.id),
        "prompt_variant": "default:mem0-abc123",
    }
    assert observed["webhook"]["event"] == "message.completed"
    assert observed["webhook"]["user_id"] == test_user.id
    assert observed["webhook"]["payload"] == {
        "conversation_id": str(conversation.id),
        "message_id": str(message_id),
        "role": "assistant",
        "model": "gpt-x",
        "content": "answer",
    }


async def test_worker_start_and_stop_are_graceful(session_factory):
    worker = outbox_worker.OutboxWorker(
        session_factory=session_factory, poll_interval_seconds=0.01
    )
    worker.start()
    assert worker.task is not None
    await worker.stop()
    assert worker.task.done()


def test_worker_flag_defaults_off_and_is_explicitly_opted_in(monkeypatch):
    monkeypatch.delenv("POST_ANSWER_OUTBOX_ENABLED", raising=False)
    assert outbox_worker.outbox_worker_enabled() is False
    for value in ("false", "0", "", "anything"):
        monkeypatch.setenv("POST_ANSWER_OUTBOX_ENABLED", value)
        assert outbox_worker.outbox_worker_enabled() is False
    for value in ("true", "TRUE", "1", "yes", "on"):
        monkeypatch.setenv("POST_ANSWER_OUTBOX_ENABLED", value)
        assert outbox_worker.outbox_worker_enabled() is True
