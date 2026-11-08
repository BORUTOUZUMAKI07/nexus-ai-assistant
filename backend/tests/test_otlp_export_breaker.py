"""The OTLP export circuit breaker.

Measured in production on 2026-11-08: the collector is a Render free instance,
so it sleeps when idle and takes ~50 s to wake, during which Render's proxy
answers 502. The SDK exporters treat that as transient, retry with backoff, and
report FAILURE — every 30 s, for logs, traces and metrics alike — so an idle
backend logged `Transient error Bad Gateway ... exporting <signal> batch`
forever and spent a thread on a service that was asleep.

The breaker stops asking: after N consecutive failures, exports are dropped
without touching the network for a cool-off, then one probe is allowed through.
These tests pin the three behaviours the fix depends on — drop during cool-off,
do not retry the dropped batch (the wrapper answers SUCCESS, not FAILURE), and
come back on its own once the collector answers again.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.app.services.observability import otel


class RecordingExporter:
    """Stand-in for an OTLP exporter: records calls, returns a fixed result."""

    def __init__(self, result: Any = "FAILURE") -> None:
        self.result = result
        self.export_calls = 0
        self.attributes_asked_for: list[str] = []
        self.shutdown_calls = 0

    def export(self, batch: Any) -> Any:
        self.export_calls += 1
        return self.result

    def force_flush(self, *args: Any, **kwargs: Any) -> bool:
        return True

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    # Mirrors the SDK's habit of reading attribution off the exporter itself
    # (PeriodicExportingMetricReader asks for `_preferred_temporality`).
    _preferred_temporality = {"counter": "cumulative"}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def breaker(monkeypatch: pytest.MonkeyPatch) -> tuple[otel._ExportBreaker, FakeClock]:
    """Install a short-cycle module breaker and share it with the wrapper."""
    clock = FakeClock()
    fresh = otel._ExportBreaker(threshold=2, cooldown_seconds=60.0, clock=clock)
    monkeypatch.setattr(otel, "_collector_breaker", fresh)
    return fresh, clock


def test_exports_are_dropped_without_reaching_a_suspended_collector(breaker):
    _, _ = breaker
    inner = RecordingExporter("FAILURE")
    wrapper = otel._CircuitBreakingExporter(inner, signal="traces", success="SUCCESS")

    assert wrapper.export([]) == "FAILURE"
    assert wrapper.export([]) == "FAILURE"  # trips the circuit at threshold=2
    calls_before = inner.export_calls

    # While suspended the export must not reach the network, and must report
    # SUCCESS — returning FAILURE would make the SDK retry the batch we just
    # decided not to send, which is the behaviour this exists to stop.
    assert wrapper.export([]) == "SUCCESS"
    assert wrapper.export([]) == "SUCCESS"
    assert inner.export_calls == calls_before, "a suspended collector was contacted"


def test_a_successful_export_leaves_the_circuit_alone(breaker):
    fresh, _ = breaker
    inner = RecordingExporter("SUCCESS")
    wrapper = otel._CircuitBreakingExporter(inner, signal="logs", success="SUCCESS")

    for _ in range(5):
        assert wrapper.export([]) == "SUCCESS"
    assert inner.export_calls == 5
    assert fresh.should_skip() is False


def test_cooloff_expiry_allows_one_probe_and_a_failure_reopens(breaker):
    fresh, clock = breaker
    inner = RecordingExporter("FAILURE")
    wrapper = otel._CircuitBreakingExporter(inner, signal="metrics", success="SUCCESS")

    wrapper.export([])
    wrapper.export([])  # circuit now open for 60 s
    assert fresh.should_skip() is True

    clock.advance(61.0)
    # One export is allowed through as a probe; it fails, so the circuit closes
    # again for another period rather than letting every batch through.
    assert wrapper.export([]) == "FAILURE"
    assert inner.export_calls == 3
    assert fresh.should_skip() is True

    clock.advance(61.0)
    inner.result = "SUCCESS"
    assert wrapper.export([]) == "SUCCESS"
    assert fresh.should_skip() is False


def test_the_wrapper_does_not_hide_the_exporter_it_wraps(breaker):
    """Regression: the SDK reads `_preferred_temporality` off the exporter.

    Composition that only forwarded the three protocol methods made metric
    wiring fail with an AttributeError inside a fail-open handler, which read as
    "telemetry silently off" rather than as a bug.
    """
    inner = RecordingExporter("SUCCESS")
    wrapper = otel._CircuitBreakingExporter(inner, signal="metrics", success="SUCCESS")

    assert wrapper._preferred_temporality == {"counter": "cumulative"}
    wrapper.shutdown()
    assert inner.shutdown_calls == 1


def test_the_breaker_is_shared_because_there_is_one_collector(breaker):
    """A failure seen on one signal must silence the other two as well."""
    _, _ = breaker
    logs = otel._CircuitBreakingExporter(RecordingExporter("FAILURE"), signal="logs", success="SUCCESS")
    traces = otel._CircuitBreakingExporter(
        RecordingExporter("FAILURE"), signal="traces", success="SUCCESS"
    )

    logs.export([])
    traces.export([])  # threshold reached across signals, not per signal
    assert otel._collector_breaker.should_skip() is True
