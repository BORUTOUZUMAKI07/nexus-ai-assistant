"""Enqueue, claim and state transitions for post-answer work.

The run path only calls ``enqueue_post_answer_tasks``: a handful of inserts with
``ON CONFLICT DO NOTHING`` on the idempotency key. Everything that can block --
cache invalidation, webhook delivery -- belongs to the worker. Keeping these
operations in one module means the key format and the status machine have a
single definition, shared by the enqueue side and the delivery side.

No raw answer, prompt, token count, webhook URL or user id is ever put in a log
or span here. The durable payload necessarily carries the ids and the (already
truncated) webhook body; those are data, not telemetry labels.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from backend.app.services.outbox.models import PostAnswerTask
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)

TASK_CACHE_INVALIDATION = "semantic_cache_invalidate"
TASK_WEBHOOK_DISPATCH = "webhook_dispatch"
TASK_TYPES = (TASK_CACHE_INVALIDATION, TASK_WEBHOOK_DISPATCH)

MAX_ATTEMPTS = 5
RETRY_BASE_SECONDS = 1.0
RETRY_CAP_SECONDS = 300.0
WEBHOOK_CONTENT_LIMIT = 2000


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def idempotency_key(run_id: UUID, message_id: UUID, task_type: str) -> str:
    """The identity of one job: (run_id, message_id, task_type)."""
    return f"{run_id}:{message_id}:{task_type}"


def retry_delay_seconds(
    attempts: int,
    *,
    base: float = RETRY_BASE_SECONDS,
    cap: float = RETRY_CAP_SECONDS,
) -> float:
    """Exponential backoff with a ceiling; attempt 1 waits one base interval."""
    return float(min(cap, base * 2 ** max(0, attempts - 1)))


def _insert_ignoring_conflicts(values: dict[str, Any], session: AsyncSession):
    """Dialect-specific INSERT ... ON CONFLICT DO NOTHING.

    Postgres is the production target; the SQLite form keeps the helper usable
    in any local harness that swaps in an in-memory engine. The constraint name
    is the one declared by both the model and ``f9a0b1c2d3e4``.
    """
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        insert = pg_insert
    elif dialect == "sqlite":
        insert = sqlite_insert
    else:  # pragma: no cover - only Postgres/SQLite are supported here
        raise RuntimeError(f"outbox: unsupported database dialect {dialect!r}")
    return (
        insert(PostAnswerTask)
        .values(**values)
        .on_conflict_do_nothing(constraint="uq_post_answer_tasks_idempotency")
        .returning(PostAnswerTask.id)
    )


async def enqueue_post_answer_tasks(
    session: AsyncSession,
    *,
    run_id: UUID,
    message_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
    model: str,
    content: str,
    prompt_variant: str = "default",
    task_types: tuple[str, ...] = TASK_TYPES,
) -> int:
    """Queue cache invalidation and webhook delivery for one persisted reply.

    Returns the number of rows actually inserted (0 on a repeat). Callers must
    treat a failure as fail-open: the assistant row has already been committed
    by then, and a missing outbox table must never turn into a failed turn.

    Memory write/mirroring is intentionally not queued here. The awaited memory
    write in ``agents/orchestrator/nodes.py`` is owned by another slice; moving
    it belongs to that work, not to this outbox.
    """
    specs: tuple[tuple[str, dict[str, Any]], ...] = (
        (
            TASK_CACHE_INVALIDATION,
            {
                "user_id": str(user_id),
                "model": model,
                "conversation_id": str(conversation_id),
                "prompt_variant": prompt_variant,
            },
        ),
        (
            TASK_WEBHOOK_DISPATCH,
            {
                "user_id": str(user_id),
                "conversation_id": str(conversation_id),
                "message_id": str(message_id),
                "model": model,
                # Same bound the existing inline webhook dispatch used. The
                # outbox row is durable state; it must not grow with the answer.
                "content": content[:WEBHOOK_CONTENT_LIMIT],
            },
        ),
    )
    inserted = 0
    now = _utcnow()
    for task_type, payload in specs:
        if task_type not in task_types:
            continue
        values = {
            "id": uuid.uuid4(),
            "run_id": run_id,
            "message_id": message_id,
            "task_type": task_type,
            "idempotency_key": idempotency_key(run_id, message_id, task_type),
            "payload": payload,
            "status": "pending",
            "attempts": 0,
            "max_attempts": MAX_ATTEMPTS,
            "available_at": now,
            "created_at": now,
        }
        result = await session.execute(_insert_ignoring_conflicts(values, session))
        inserted_rows = result.all()
        if not inserted_rows:
            # A repeated enqueue is a no-op; logging it would double the signal.
            continue
        inserted += len(inserted_rows)
        logger.info(
            "outbox.enqueue",
            run_id=str(run_id),
            message_id=str(message_id),
            task_types=[task_type],
        )
    return inserted


def claim_statement(limit: int = 1):
    """The single claim query: due pending rows, skipped when already locked."""
    return (
        select(PostAnswerTask)
        .where(
            PostAnswerTask.status == "pending",
            PostAnswerTask.available_at <= _utcnow(),
        )
        .order_by(PostAnswerTask.available_at, PostAnswerTask.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


async def claim_next(session: AsyncSession) -> PostAnswerTask | None:
    """Atomically take one due row. Safe for any number of concurrent workers."""
    task = (await session.exec(claim_statement())).scalars().first()
    if task is None:
        return None
    task.status = "running"
    task.locked_at = _utcnow()
    task.attempts += 1
    session.add(task)
    await session.commit()
    logger.info(
        "outbox.claim",
        task_id=str(task.id),
        task_type=task.task_type,
        run_id=str(task.run_id),
        attempts=task.attempts,
    )
    return task


async def mark_succeeded(session: AsyncSession, task: PostAnswerTask) -> None:
    task.status = "succeeded"
    task.locked_at = None
    task.finished_at = _utcnow()
    session.add(task)
    await session.commit()


async def mark_failed(session: AsyncSession, task: PostAnswerTask, error: str) -> None:
    """Retry with backoff, or dead-letter when the attempt budget is spent.

    ``failed`` is assigned explicitly to make the state machine readable, but
    the row is committed in its *next* state: a crash between two commits would
    otherwise leave work stuck in a status no worker claims.
    """
    task.status = "failed"
    task.last_error = error[:500]
    task.locked_at = None
    if task.attempts >= task.max_attempts:
        task.status = "dead_letter"
        task.finished_at = _utcnow()
        logger.warning(
            "outbox.dead_letter",
            task_id=str(task.id),
            task_type=task.task_type,
            run_id=str(task.run_id),
            attempts=task.attempts,
        )
    else:
        delay = retry_delay_seconds(task.attempts)
        task.available_at = _utcnow() + timedelta(seconds=delay)
        task.status = "pending"
        logger.warning(
            "outbox.retry",
            task_id=str(task.id),
            task_type=task.task_type,
            run_id=str(task.run_id),
            attempts=task.attempts,
            next_delay_seconds=delay,
        )
    session.add(task)
    await session.commit()
