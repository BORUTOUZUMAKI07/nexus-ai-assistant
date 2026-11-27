"""Outbox delivery metrics must stay bounded and fail open.

The outbox already has durable state transitions and structured logs. This
slice adds RED telemetry without putting a task id, run id, payload or error
message into a label: only the bounded ``stage`` / ``task_type`` / ``outcome``
triple is emitted. Metric wiring must never change whether a job was marked
succeeded or failed.

These tests use fakes so they run without Postgres or Docker; the state-machine
tests in ``test_post_answer_outbox.py`` own the database behaviour.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend.app.services.outbox import worker as outbox_worker

TASK_ID = uuid4()
RUN_ID = uuid4()
TASK_TYPE = "webhook_dispatch"


class _MetricsRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | float, float | int, dict]] = []

    def increment(self, name: str, count: int = 1, attributes: dict | None = None) -> None:
        self.calls.append(("increment", count, 0, dict(attributes or {})))

    def record_error(self, name: str, count: int = 1, attributes: dict | None = None) -> None:
        self.calls.append(("record_error", count, 0, dict(attributes or {})))

    def record_latency(self, name: str, ms: float, attributes: dict | None = None) -> None:
        self.calls.append(("record_latency", 1, ms, dict(attributes or {})))


class _RaisingMetrics:
    """Simulates a broken metrics backend; delivery must not notice it."""

    def increment(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("metrics down")

    def record_error(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("metrics down")

    def record_latency(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("metrics down")


class _LogRecorder:
    def info(self, *_args: object, **_kwargs: object) -> None:
        pass

    def warning(self, *_args: object, **_kwargs: object) -> None:
        pass

    def error(self, *_args: object, **_kwargs: object) -> None:
        pass

    def debug(self, *_args: object, **_kwargs: object) -> None:
        pass


def _no_span(_name: str, _attrs: dict | None = None):
    @asynccontextmanager
    async def _context():
        yield {}

    return _context()


def _prepare(
    monkeypatch: pytest.MonkeyPatch,
    metrics: object,
    handler,
    *,
    attempts: int = 1,
    max_attempts: int = 5,
):
    task = SimpleNamespace(
        id=TASK_ID,
        task_type=TASK_TYPE,
        run_id=RUN_ID,
        payload={"event": "message.completed"},
        attempts=attempts,
        max_attempts=max_attempts,
    )
    succeeded: list[object] = []
    failed: list[tuple[object, str]] = []

    async def _claim_next(_session):
        return task

    async def _mark_succeeded(_session, claimed):
        succeeded.append(claimed)

    async def _mark_failed(_session, claimed, error: str):
        failed.append((claimed, error))

    monkeypatch.setattr(outbox_worker, "claim_next", _claim_next)
    monkeypatch.setattr(outbox_worker, "mark_succeeded", _mark_succeeded)
    monkeypatch.setattr(outbox_worker, "mark_failed", _mark_failed)
    monkeypatch.setattr(outbox_worker, "metrics_collector", metrics)
    monkeypatch.setattr(outbox_worker, "logger", _LogRecorder())
    monkeypatch.setattr(outbox_worker, "trace_span", _no_span)

    worker = outbox_worker.OutboxWorker(
        session_factory=lambda: None,
        handlers={TASK_TYPE: handler},
    )
    return worker, task, succeeded, failed


async def test_successful_dispatch_records_a_bounded_success_sample(monkeypatch):
    metrics = _MetricsRecorder()

    async def _handler(_session, _payload):
        return None

    worker, _task, succeeded, failed = _prepare(monkeypatch, metrics, _handler)

    assert await worker.process_one(None) is True

    counters = [call for call in metrics.calls if call[0] == "increment"]
    errors = [call for call in metrics.calls if call[0] == "record_error"]
    latencies = [call for call in metrics.calls if call[0] == "record_latency"]
    assert len(counters) == 1
    assert errors == []
    assert len(latencies) == 1
    assert latencies[0][2] >= 0

    expected = {"stage": "outbox", "task_type": TASK_TYPE, "outcome": "success"}
    assert counters[0][3] == expected
    assert latencies[0][3] == expected
    assert str(TASK_ID) not in str(metrics.calls)
    assert str(RUN_ID) not in str(metrics.calls)
    assert len(succeeded) == 1
    assert failed == []


async def test_retryable_failure_records_retried_without_leaking_the_error(monkeypatch):
    metrics = _MetricsRecorder()

    async def _handler(_session, _payload):
        raise RuntimeError("webhook upstream exploded")

    worker, _task, succeeded, failed = _prepare(monkeypatch, metrics, _handler)

    assert await worker.process_one(None) is True

    errors = [call for call in metrics.calls if call[0] == "record_error"]
    assert len(errors) == 1
    assert errors[0][3] == {
        "stage": "outbox",
        "task_type": TASK_TYPE,
        "outcome": "retried",
    }
    assert "webhook upstream exploded" not in str(metrics.calls)
    assert succeeded == []
    assert len(failed) == 1
    assert failed[0][1] == "webhook upstream exploded"


async def test_final_failure_records_dead_letter_not_retried(monkeypatch):
    metrics = _MetricsRecorder()

    async def _handler(_session, _payload):
        raise RuntimeError("permanent failure")

    worker, _task, succeeded, failed = _prepare(
        monkeypatch, metrics, _handler, attempts=5, max_attempts=5
    )

    assert await worker.process_one(None) is True

    errors = [call for call in metrics.calls if call[0] == "record_error"]
    assert len(errors) == 1
    assert errors[0][3]["outcome"] == "dead_letter"
    assert "permanent failure" not in str(metrics.calls)
    assert succeeded == []
    assert len(failed) == 1


async def test_metric_failure_never_changes_the_job_result(monkeypatch):
    async def _handler(_session, _payload):
        return None

    worker, _task, succeeded, failed = _prepare(monkeypatch, _RaisingMetrics(), _handler)

    assert await worker.process_one(None) is True
    assert len(succeeded) == 1
    assert failed == []


async def test_no_due_work_emits_no_sample(monkeypatch):
    metrics = _MetricsRecorder()

    async def _claim_next(_session):
        return None

    monkeypatch.setattr(outbox_worker, "claim_next", _claim_next)
    monkeypatch.setattr(outbox_worker, "metrics_collector", metrics)
    worker = outbox_worker.OutboxWorker(session_factory=lambda: None, handlers={})

    assert await worker.process_one(None) is False
    assert metrics.calls == []
