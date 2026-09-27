"""
Celery-backed event publisher (the adapter used today).

Maps a ``DomainEvent.event_type`` to a registered Celery task name via the
``EVENT_TASK_ROUTING`` table in ``registry.py``. Dispatch is fail-safe: a
broker outage, unknown task, or mapping miss never raises into the request
path — producers degrade (e.g. the file router's sync-ingest fallback) instead
of crashing.
"""
from __future__ import annotations

from typing import Any

import structlog
from backend.app.infrastructure.events.base import DomainEvent, EventPublisher
from backend.app.infrastructure.events.registry import EVENT_TASK_ROUTING

logger = structlog.get_logger(__name__)


class CeleryPublisher(EventPublisher):
    """Dispatch events to the Celery task named in the routing table."""

    async def publish(self, event: DomainEvent) -> bool:
        task_name = EVENT_TASK_ROUTING.get(event.event_type)
        if not task_name:
            # No consumer registered for this event type → by design a no-op.
            # This mirrors the outbox's "commit the row, let relayers decide";
            # until a second consumer exists we simply drop it.
            logger.debug("event_no_consumer_ignored", event_type=event.event_type)
            return True

        try:
            # celery has no py.typed marker; the untyped import is handled by
            # the CI mypy --follow-imports=silent mode (silent import).
            from celery import current_app

            dispatched = self._dispatch(current_app, task_name, event)
            if dispatched:
                logger.info(
                    "event_dispatched",
                    event_type=event.event_type,
                    task=task_name,
                    aggregate_id=event.aggregate_id,
                )
            return dispatched
        except Exception as exc:
            logger.warning(
                "event_dispatch_failed_fail_open",
                event_type=event.event_type,
                task=task_name,
                error=str(exc),
            )
            return False

    @staticmethod
    def _dispatch(app: Any, task_name: str, event: DomainEvent) -> bool:
        """Synchronous hand-off into Celery's send_task (no broker round-trip
        until the worker actually picks the message up).

        Returns True once the message was handed to send_task. ``app`` is the
        Celery app instance (injectable for tests).
        """
        app.send_task(
            task_name,
            args=[event.aggregate_id],
            kwargs={"payload": dict(event.payload)},
        )
        return True


_event_publisher: EventPublisher | None = None


def get_event_publisher() -> EventPublisher:
    """Return the active EventPublisher (Celery today, outbox later).

    Swapping the seam's backend later is a config flip here — call sites never
    know which implementation they are talking to.
    """
    global _event_publisher
    if _event_publisher is None:
        _event_publisher = CeleryPublisher()
    return _event_publisher


def set_event_publisher(publisher: EventPublisher | None) -> None:
    """Test hook: inject a fake/recording publisher, or None to reset."""
    global _event_publisher
    _event_publisher = publisher
