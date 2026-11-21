"""The trace store reads persisted evaluation logs and fails open."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.app.domain.usage.models import EvaluationLog
from backend.app.services.observability.trace_store import get_trace_span
from backend.tests.fakes import FakeSession


def _log(**kwargs):
    defaults = {
        "trace_id": "trace-1",
        "metric_name": "faithfulness",
        "score": 0.9,
        "passed": True,
        "evaluator": "heuristic",
        "created_at": datetime.now(UTC).replace(tzinfo=None),
    }
    defaults.update(kwargs)
    return EvaluationLog(**defaults)


@pytest.mark.asyncio
async def test_unknown_trace_returns_none():
    assert await get_trace_span("missing", FakeSession()) is None


@pytest.mark.asyncio
async def test_read_failure_fails_open(monkeypatch):
    session = FakeSession()

    async def _broken_exec(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(session, "exec", _broken_exec)

    assert await get_trace_span("trace-1", session) is None


@pytest.mark.asyncio
async def test_span_includes_events_and_metadata():
    session = FakeSession()
    session.seed(
        EvaluationLog,
        [
            _log(
                metric_name="cost_usd",
                score=0.125,
                metadata_json={"model": "gpt-4o-mini", "tokens": 42},
            ),
            _log(metric_name="latency_ms", score=250.0, passed=False),
        ],
    )

    span = await get_trace_span("trace-1", session)

    assert span is not None
    assert span["trace_id"] == "trace-1"
    assert span["cost_usd"] == 0.125
    assert span["latency_ms"] == 250.0
    assert span["tokens"] == 42
    assert span["model"] == "gpt-4o-mini"
    assert [event["metric"] for event in span["events"]] == ["cost_usd", "latency_ms"]
    assert span["events"][1]["passed"] is False
