"""
Event-type → consumer routing table.

This is the ONLY place that maps a domain event to which system consumes it.
Today every event maps to exactly one Celery task (the "one consumer per
event" invariant that keeps the full outbox unnecessary). The day a second
independent consumer wants the same event, the outbox adapter replaces this
lookup with an ``outbox_events`` write — without touching any call site.
"""
from __future__ import annotations

# event_type -> Celery task name
EVENT_TASK_ROUTING: dict[str, str] = {
    "file.index_requested": "tasks.process_file_indexing",
}
