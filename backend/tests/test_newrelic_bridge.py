"""
Unit tests for the New Relic OTLP metrics bridge (env-gated, fail-open).

Covers the safe no-op paths the bridge must honour, plus the observable
callbacks that mirror the in-process collector into OTLP. The optional
[observability] extras may or may not be installed depending on the venv;
every assertion here is robust to either world — no network, no OTel
pipeline, no settings leakage.
"""
import importlib.util

import pytest
from backend.app.core.config import settings
from backend.app.services.observability import newrelic
from backend.app.services.observability.metrics import metrics_collector

OTEL_INSTALLED = importlib.util.find_spec("opentelemetry") is not None


def _set(name: str, value) -> None:
    setattr(settings, name, value)


def test_bridge_disabled_returns_none():
    original = settings.NEW_RELIC_ENABLED
    try:
        _set("NEW_RELIC_ENABLED", False)
        assert newrelic.start_new_relic_export() is None
    finally:
        _set("NEW_RELIC_ENABLED", original)


def test_bridge_enabled_but_unkeyed_returns_none():
    original_enabled = settings.NEW_RELIC_ENABLED
    original_key = settings.NEW_RELIC_LICENSE_KEY
    try:
        _set("NEW_RELIC_ENABLED", True)
        _set("NEW_RELIC_LICENSE_KEY", None)
        assert newrelic.start_new_relic_export() is None
    finally:
        _set("NEW_RELIC_ENABLED", original_enabled)
        _set("NEW_RELIC_LICENSE_KEY", original_key)


def test_bridge_enabled_keyed_wires_shutdown_safely(monkeypatch):
    # Extras installed → the bridge starts and returns a shutdown callable
    # (hermetic: fake the provider builder so no OTel exporter or network is
    # involved). Extras missing (CI) → clean no-op returning None.
    original_enabled = settings.NEW_RELIC_ENABLED
    original_key = settings.NEW_RELIC_LICENSE_KEY
    try:
        _set("NEW_RELIC_ENABLED", True)
        _set("NEW_RELIC_LICENSE_KEY", "ingest-key")

        if not OTEL_INSTALLED:
            assert newrelic.start_new_relic_export() is None
            return

        calls: list[str] = []

        class FakeProvider:
            def shutdown(self) -> None:
                calls.append("shutdown")

        monkeypatch.setattr(newrelic, "_build_exporter_and_provider", lambda: FakeProvider())
        shutdown = newrelic.start_new_relic_export()
        assert callable(shutdown)
        shutdown()
        shutdown()  # idempotent — must not raise
        assert calls == ["shutdown", "shutdown"]
    finally:
        _set("NEW_RELIC_ENABLED", original_enabled)
        _set("NEW_RELIC_LICENSE_KEY", original_key)


@pytest.mark.skipif(not OTEL_INSTALLED, reason="requires the optional [observability] extras")
def test_observable_callbacks_mirror_collector():
    # The callbacks return an iterable of observations (current OTel API — the
    # SDK invokes the callback and iterates the result). They only read the
    # shared in-process collector, so they work without a live OTel pipeline.
    metrics_collector.increment("llm_completion", count=3)
    metrics_collector.record_latency("llm_completion", 120.0)
    metrics_collector.record_latency("llm_completion", 340.0)
    metrics_collector.record_error("timeout")

    requests = newrelic._observe_requests(None)
    errors = newrelic._observe_errors(None)
    latency = newrelic._observe_latency(None)

    # OTel Observations expose .value and .attributes.
    req_by_op = {o.attributes["operation"]: o.value for o in requests}
    assert req_by_op.get("llm_completion") == 3

    err_by_name = {o.attributes["error"]: o.value for o in errors}
    assert err_by_name.get("timeout") == 1

    lat_by_q = {
        o.attributes["quantile"]: o.value
        for o in latency
        if o.attributes["operation"] == "llm_completion"
    }
    # The collector computes percentiles as sorted_samples[int(n * q)], so with
    # n=2 every quantile resolves to the upper sample (340 ms). The bridge must
    # mirror that math exactly — p50/p95/p99 → 0.34 s, avg(120, 340) → 0.23 s.
    assert lat_by_q["avg"] == 0.23
    assert lat_by_q["p50"] == 0.34
    assert lat_by_q["p95"] == 0.34
    assert lat_by_q["p99"] == 0.34
