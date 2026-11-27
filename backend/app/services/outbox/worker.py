"""Background consumer for durable post-answer work.

Started from the application lifespan, stopped on shutdown. The loop opens a
fresh session per poll, claims with ``FOR UPDATE SKIP LOCKED``, and commits each
state transition separately: a task that dies mid-delivery is retried with
backoff rather than wedging the queue.

Disabled by default so the code can be deployed *before* the migration is
applied. Set ``POST_ANSWER_OUTBOX_ENABLED=true`` once ``f9a0b1c2d3e4`` is live.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from typing import Any

import structlog
from backend.app.services.observability.metrics import (
    metrics_collector,
    record_outbox_outcome,
)
from backend.app.services.observability.tracing import trace_span
from backend.app.services.outbox.handlers import HANDLERS
from backend.app.services.outbox.service import claim_next, mark_failed, mark_succeeded
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


def _record_outcome(task_type: str, outcome: str, duration_ms: float) -> None:
    """Best-effort RED sample — a broken metric sink must not change a job."""
    try:
        record_outbox_outcome(
            task_type, outcome, duration_ms, collector=metrics_collector
        )
    except Exception:  # noqa: BLE001 - observability must not raise
        logger.debug("outbox_metric_failed", outcome=outcome)


def outbox_worker_enabled() -> bool:
    """Feature flag for worker startup, default OFF.

    ``config.py`` is not part of this slice, so the flag is read from the
    process environment instead of the settings singleton. It is deliberately
    opt-in: before the migration is applied the table does not exist, and a
    worker polling it would only produce noise. The enqueue side is fail-open
    either way, so enabling the flag later needs no code change.
    """
    return os.getenv("POST_ANSWER_OUTBOX_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class OutboxWorker:
    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        poll_interval_seconds: float = 1.0,
        handlers: dict[str, Any] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.poll_interval_seconds = poll_interval_seconds
        self.handlers = handlers if handlers is not None else HANDLERS
        self._stop = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start one consumer loop; repeated starts are no-ops."""
        if self.task is not None and not self.task.done():
            return
        self._stop.clear()
        self.task = asyncio.create_task(self._run(), name="nexus-post-answer-outbox")

    async def stop(self) -> None:
        """Ask the loop to finish, then wait for it (bounded) on shutdown."""
        self._stop.set()
        task = self.task
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                async with self.session_factory() as session:
                    while not self._stop.is_set():
                        if not await self.process_one(session):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "outbox_poll_failed", error_type=type(exc).__name__
                )
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.poll_interval_seconds
                )
            except TimeoutError:
                pass

    async def process_one(self, session: AsyncSession) -> bool:
        """Claim and deliver one task. Returns False when there is no due work."""
        task = await claim_next(session)
        if task is None:
            return False
        handler = self.handlers.get(task.task_type)
        if handler is None:
            logger.warning(
                "outbox.unknown_task_type",
                task_id=str(task.id),
                task_type=task.task_type,
                run_id=str(task.run_id),
            )
            await mark_failed(session, task, "unknown task type")
            _record_outcome(task.task_type, "dropped", 0.0)
            return True

        started = time.perf_counter()
        try:
            async with trace_span(
                "outbox.deliver",
                {
                    "task_id": str(task.id),
                    "task_type": task.task_type,
                    "run_id": str(task.run_id),
                },
            ):
                await handler(session, task.payload or {})
        except asyncio.CancelledError:
            # A shutdown must not turn a claimed row into a retry: leave it
            # running so a later sweep can decide, rather than committing a
            # half-delivery as success.
            raise
        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            outcome = (
                "dead_letter"
                if task.attempts >= task.max_attempts
                else "retried"
            )
            _record_outcome(task.task_type, outcome, duration_ms)
            await mark_failed(session, task, str(exc)[:500])
            return True
        duration_ms = (time.perf_counter() - started) * 1000
        await mark_succeeded(session, task)
        _record_outcome(task.task_type, "success", duration_ms)
        logger.info(
            "outbox.deliver",
            task_id=str(task.id),
            task_type=task.task_type,
            run_id=str(task.run_id),
            duration_ms=round(duration_ms, 2),
        )
        return True


async def process_one(session: AsyncSession, handlers: dict[str, Any] | None = None) -> bool:
    """Convenience wrapper: claim and deliver one task with the default handlers."""
    return await OutboxWorker(session_factory=lambda: None, handlers=handlers).process_one(session)


__all__ = ["OutboxWorker", "outbox_worker_enabled", "process_one"]
