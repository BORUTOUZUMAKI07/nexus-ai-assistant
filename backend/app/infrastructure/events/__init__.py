"""
Event publishing seam (the "socket" for a future transactional outbox).

The outbox pattern is deliberately deferred (see docs/hld-patterns-coverage.md
row 6). This module is the cheap prep layer the audit recommended instead:
a single ``EventPublisher`` interface with two pluggable adapters —

  * ``CeleryPublisher``   — dispatch a domain event to a registered Celery task
                            (used today; at most one consumer per event).
  * ``OutboxPublisher``   — NOT implemented yet; the adapter that will write the
                            event to an ``outbox_events`` table and fan it out to
                            ≥2 consumers the day a second consumer appears.

Call sites depend on the *interface*, so swapping the active adapter later is a
one-line config flip, not a rewrite.
"""
from backend.app.infrastructure.events.base import DomainEvent, EventPublisher  # noqa: F401
from backend.app.infrastructure.events.celery_publisher import (  # noqa: F401
    CeleryPublisher,
    get_event_publisher,
)
