"""
Unit tests for the EventPublisher seam — the cheap "socket" that keeps a full
transactional outbox deferred (docs/hld-patterns-coverage.md row 6).

Verifies the swap-the-adapter contract: call sites depend on the interface;
the Celery adapter routes events through the registry; dispatch is fail-open.
"""
from __future__ import annotations

import pytest
from backend.app.infrastructure.events.base import DomainEvent
from backend.app.infrastructure.events.celery_publisher import (
    CeleryPublisher,
    get_event_publisher,
    set_event_publisher,
)
from backend.app.infrastructure.events.registry import EVENT_TASK_ROUTING


class RecordingPublisher:
    """Test double recording events instead of touching Celery."""

    def __init__(self):
        self.events: list[DomainEvent] = []
        self.fail = False

    async def publish(self, event: DomainEvent) -> bool:
        if self.fail:
            raise RuntimeError("broker down")
        self.events.append(event)
        return True


@pytest.fixture(autouse=True)
def _reset_publisher():
    yield
    set_event_publisher(None)


def test_registry_maps_file_index_event_to_worker():
    assert EVENT_TASK_ROUTING["file.index_requested"] == "tasks.process_file_indexing"


@pytest.mark.asyncio
async def test_publish_routing_uses_registry_task_name(monkeypatch):
    """The Celery adapter must dispatch via send_task to the registered task."""
    captured: list[tuple[str, list, dict]] = []

    def fake_dispatch(app, task_name, event):
        captured.append((task_name, [event.aggregate_id], {"payload": dict(event.payload)}))
        return True

    monkeypatch.setattr(CeleryPublisher, "_dispatch", staticmethod(fake_dispatch))

    publisher = CeleryPublisher()
    dispatched = await publisher.publish(
        DomainEvent(
            event_type="file.index_requested",
            aggregate_id="file-1",
            user_id=None,
            payload={"file_id": "file-1"},
        )
    )

    assert dispatched is True
    assert captured == [("tasks.process_file_indexing", ["file-1"], {"payload": {"file_id": "file-1"}})]


@pytest.mark.asyncio
async def test_publish_unregistered_event_is_safe_noop(monkeypatch):
    captured: list = []

    def fake_dispatch(app, task_name, event):
        captured.append(task_name)
        return True

    monkeypatch.setattr(CeleryPublisher, "_dispatch", staticmethod(fake_dispatch))

    publisher = CeleryPublisher()
    dispatched = await publisher.publish(
        DomainEvent(event_type="unregistered.event", aggregate_id="x")
    )

    assert dispatched is True  # no consumer → safe ignore, not an error
    assert captured == []


@pytest.mark.asyncio
async def test_publish_fail_open_on_broker_outage(monkeypatch):
    def boom_dispatch(app, task_name, event):
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr(CeleryPublisher, "_dispatch", staticmethod(boom_dispatch))

    publisher = CeleryPublisher()
    dispatched = await publisher.publish(
        DomainEvent(event_type="file.index_requested", aggregate_id="file-1")
    )

    # Fail-open: returns False so the caller's sync-ingest fallback runs.
    assert dispatched is False


@pytest.mark.asyncio
async def test_get_event_publisher_returns_singleton():
    a = get_event_publisher()
    b = get_event_publisher()
    assert a is b
    assert isinstance(a, CeleryPublisher)


@pytest.mark.asyncio
async def test_set_event_publisher_test_hook_swaps_implementation():
    recorder = RecordingPublisher()
    set_event_publisher(recorder)

    publisher = get_event_publisher()
    assert publisher is recorder

    dispatched = await publisher.publish(
        DomainEvent(event_type="file.index_requested", aggregate_id="f1")
    )
    assert dispatched is True
    assert recorder.events[0].aggregate_id == "f1"
