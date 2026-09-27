"""
Event publisher contracts (dependency inversion for outbound events).

``EventPublisher`` is the seam the audit recommended as the cheap alternative
to building a full transactional outbox today: producers declare the intent
("a file was uploaded"), not the transport. The active implementation decides
how it is delivered (Celery now, outbox later).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class DomainEvent:
    """A facts-already-committed domain event.

    ``event_type`` is the routing key consumers subscribe to (e.g.
    ``"file.index_requested"``). ``aggregate_id`` is the owning entity id used
    for idempotency if a future second consumer appears.
    """

    event_type: str
    aggregate_id: str
    user_id: UUID | None = None
    org_id: UUID | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class EventPublisher(ABC):
    """Publish a committed domain event to whichever backend is active.

    Implementations MUST be fail-safe (never raise into the request path) and
    SHOULD be at-most-once by default; stronger delivery guarantees (exactly
    once, dedup, replay) are the outbox adapter's contract, not this one's.
    """

    @abstractmethod
    async def publish(self, event: DomainEvent) -> bool:
        """Deliver ``event`` to its registered consumers.

        Returns True when the event was handed to at least one consumer path
        (or the event has no registered consumers and was safely ignored).
        """
        raise NotImplementedError
