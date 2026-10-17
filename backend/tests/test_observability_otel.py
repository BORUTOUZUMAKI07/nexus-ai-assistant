"""
Unit tests for the Layer-1 OTLP traces + metrics export (env-gated, fail-open).

Sibling of `test_otlp_logs.py`; together they prove all three signals ship to the
Layer-2 collector over OTLP/HTTP with the same Basic auth, that the whole surface
is a strict no-op unless it is switched on, and that the app's own call paths
(``trace_span``, ``MetricsCollector``) reach real OTel spans/instruments.

Hermetic: every test that would export replaces the OTLP HTTP exporter with an
in-memory fake (or injects an in-memory reader/exporter), so no network happens
while the real SDK pipeline (provider → processor/instruments) still runs.
"""
import asyncio
import base64
import importlib.util

import pytest
from backend.app.core.config import settings
from backend.app.services.observability import logs as otlp_logs
from backend.app.services.observability import metrics as metrics_mod
from backend.app.services.observability import otel
from backend.app.services.observability import tracing as tracing_mod

OTEL_INSTALLED = importlib.util.find_spec("opentelemetry") is not None

requires_otel = pytest.mark.skipif(
    not OTEL_INSTALLED, reason="[observability] extras not installed"
)


def _set(name: str, value) -> None:
    setattr(settings, name, value)


def _await(coro):
    """Run a coroutine from a sync test.

    pytest-randomly reorders the suite, and a preceding test that used
    ``asyncio.run`` leaves the thread loop unset — hence the RuntimeError guard
    (every ``_await`` helper in this repo carries it; see AGENTS.md §7).
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@pytest.fixture(autouse=True)
def _reset_otel_state():
    """Each test starts and ends with no provider wired (traces, metrics, logs)."""
    otel.shutdown_otel()
    otlp_logs.shutdown_otlp_log_handler()
    yield
    otel.shutdown_otel()
    otlp_logs.shutdown_otlp_log_handler()


# ── auth + endpoints (pure, no SDK) ─────────────────────────────────────────


def test_no_auth_header_when_no_user_configured():
    original = settings.OTLP_AUTH_USER
    try:
        _set("OTLP_AUTH_USER", "")
        assert otel.otlp_auth_headers() is None
    finally:
        _set("OTLP_AUTH_USER", original)


def test_auth_header_is_basic_base64_of_user_and_password():
    original_user = settings.OTLP_AUTH_USER
    original_password = settings.OTLP_AUTH_PASSWORD
    try:
        _set("OTLP_AUTH_USER", "nexus")
        _set("OTLP_AUTH_PASSWORD", "s3cret")
        expected = base64.b64encode(b"nexus:s3cret").decode("ascii")
        assert otel.otlp_auth_headers() == {"Authorization": f"Basic {expected}"}
    finally:
        _set("OTLP_AUTH_USER", original_user)
        _set("OTLP_AUTH_PASSWORD", original_password)


def test_blank_user_is_treated_as_no_auth():
    original = settings.OTLP_AUTH_USER
    try:
        _set("OTLP_AUTH_USER", "   ")
        assert otel.otlp_auth_headers() is None
    finally:
        _set("OTLP_AUTH_USER", original)


def test_endpoint_builds_the_signal_paths():
    original = settings.NEW_RELIC_OTLP_ENDPOINT
    try:
        _set("NEW_RELIC_OTLP_ENDPOINT", "http://localhost:4318/")
        assert otel._signal_endpoint("traces") == "http://localhost:4318/v1/traces"
        assert otel._signal_endpoint("metrics") == "http://localhost:4318/v1/metrics"
    finally:
        _set("NEW_RELIC_OTLP_ENDPOINT", original)


# ── gating: a strict no-op unless switched on ───────────────────────────────


def test_disabled_export_is_a_noop():
    original = settings.NEW_RELIC_ENABLED
    try:
        _set("NEW_RELIC_ENABLED", False)
        assert otel.setup_otlp_tracing() is False
        assert otel.setup_otlp_metrics() is False
        assert otel.get_tracer("x") is None
        assert otel.get_meter("x") is None
    finally:
        _set("NEW_RELIC_ENABLED", original)


def test_enabled_without_endpoint_is_a_noop():
    original_enabled = settings.NEW_RELIC_ENABLED
    original_endpoint = settings.NEW_RELIC_OTLP_ENDPOINT
    try:
        _set("NEW_RELIC_ENABLED", True)
        _set("NEW_RELIC_OTLP_ENDPOINT", "")
        assert otel.setup_otlp_tracing() is False
        assert otel.setup_otlp_metrics() is False
        assert otel.get_tracer("x") is None
        assert otel.get_meter("x") is None
    finally:
        _set("NEW_RELIC_ENABLED", original_enabled)
        _set("NEW_RELIC_OTLP_ENDPOINT", original_endpoint)


# ── exporter wiring: endpoint + shared auth reach every signal ──────────────


@requires_otel
def test_span_exporter_receives_endpoint_and_basic_auth(monkeypatch):
    original_enabled = settings.NEW_RELIC_ENABLED
    original_endpoint = settings.NEW_RELIC_OTLP_ENDPOINT
    original_user = settings.OTLP_AUTH_USER
    original_password = settings.OTLP_AUTH_PASSWORD
    captured: dict = {}

    class FakeExporter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def export(self, spans):
            return None

        def shutdown(self):
            pass

    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
        FakeExporter,
    )
    try:
        _set("NEW_RELIC_ENABLED", True)
        _set("NEW_RELIC_OTLP_ENDPOINT", "http://localhost:4318")
        _set("OTLP_AUTH_USER", "nexus")
        _set("OTLP_AUTH_PASSWORD", "nexus")
        assert otel.setup_otlp_tracing() is True
        assert captured["endpoint"] == "http://localhost:4318/v1/traces"
        expected = base64.b64encode(b"nexus:nexus").decode("ascii")
        assert captured["headers"] == {"Authorization": f"Basic {expected}"}
        assert otel.get_tracer("x") is not None
    finally:
        _set("NEW_RELIC_ENABLED", original_enabled)
        _set("NEW_RELIC_OTLP_ENDPOINT", original_endpoint)
        _set("OTLP_AUTH_USER", original_user)
        _set("OTLP_AUTH_PASSWORD", original_password)


@requires_otel
def test_metric_exporter_receives_endpoint_and_basic_auth(monkeypatch):
    original_enabled = settings.NEW_RELIC_ENABLED
    original_endpoint = settings.NEW_RELIC_OTLP_ENDPOINT
    original_user = settings.OTLP_AUTH_USER
    original_password = settings.OTLP_AUTH_PASSWORD
    captured: dict = {}
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter as RealMetricExporter,
    )

    class FakeExporter(RealMetricExporter):
        """Subclasses the real exporter so the SDK's temporality lookup works."""

        def __init__(self, **kwargs):
            captured.update(kwargs)
            super().__init__(**kwargs)

        def export(self, *args, **kwargs):
            return None

        def shutdown(self):
            pass

    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter",
        FakeExporter,
    )
    try:
        _set("NEW_RELIC_ENABLED", True)
        _set("NEW_RELIC_OTLP_ENDPOINT", "http://localhost:4318")
        _set("OTLP_AUTH_USER", "nexus")
        _set("OTLP_AUTH_PASSWORD", "nexus")
        assert otel.setup_otlp_metrics() is True
        assert captured["endpoint"] == "http://localhost:4318/v1/metrics"
        expected = base64.b64encode(b"nexus:nexus").decode("ascii")
        assert captured["headers"] == {"Authorization": f"Basic {expected}"}
        assert otel.get_meter("x") is not None
    finally:
        _set("NEW_RELIC_ENABLED", original_enabled)
        _set("NEW_RELIC_OTLP_ENDPOINT", original_endpoint)
        _set("OTLP_AUTH_USER", original_user)
        _set("OTLP_AUTH_PASSWORD", original_password)


@requires_otel
def test_log_exporter_shares_the_same_auth_header(monkeypatch):
    """One credential, one shape — logs authenticate exactly like traces/metrics."""
    original_enabled = settings.NEW_RELIC_ENABLED
    original_endpoint = settings.NEW_RELIC_OTLP_ENDPOINT
    original_user = settings.OTLP_AUTH_USER
    original_password = settings.OTLP_AUTH_PASSWORD
    captured: dict = {}

    class FakeExporter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def export(self, log_data_list):
            return None

        def shutdown(self):
            pass

    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http._log_exporter.OTLPLogExporter",
        FakeExporter,
    )
    try:
        _set("NEW_RELIC_ENABLED", True)
        _set("NEW_RELIC_OTLP_ENDPOINT", "http://localhost:4318")
        _set("OTLP_AUTH_USER", "nexus")
        _set("OTLP_AUTH_PASSWORD", "nexus")
        assert otlp_logs.setup_otlp_log_handler() is not None
        assert captured["endpoint"] == "http://localhost:4318/v1/logs"
        expected = base64.b64encode(b"nexus:nexus").decode("ascii")
        assert captured["headers"] == {"Authorization": f"Basic {expected}"}
    finally:
        _set("NEW_RELIC_ENABLED", original_enabled)
        _set("NEW_RELIC_OTLP_ENDPOINT", original_endpoint)
        _set("OTLP_AUTH_USER", original_user)
        _set("OTLP_AUTH_PASSWORD", original_password)


@requires_otel
def test_resource_carries_service_identity():
    resource = otel._resource()
    attributes = resource.attributes
    assert attributes["service.name"] == settings.PROJECT_NAME
    assert attributes["service.version"] == settings.SERVICE_VERSION
    assert attributes["deployment.environment"] == settings.ENVIRONMENT


# ── fail-open + lifecycle ───────────────────────────────────────────────────


@requires_otel
def test_setup_is_fail_open_when_resource_build_fails(monkeypatch):
    """A broken provider never raises out of startup — it logs and returns False."""
    original_enabled = settings.NEW_RELIC_ENABLED
    original_endpoint = settings.NEW_RELIC_OTLP_ENDPOINT

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(otel, "_resource", _boom)
    try:
        _set("NEW_RELIC_ENABLED", True)
        _set("NEW_RELIC_OTLP_ENDPOINT", "http://localhost:4318")
        assert otel.setup_otlp_tracing() is False
        assert otel.setup_otlp_metrics() is False
    finally:
        _set("NEW_RELIC_ENABLED", original_enabled)
        _set("NEW_RELIC_OTLP_ENDPOINT", original_endpoint)


@requires_otel
def test_shutdown_is_idempotent_and_rewire_uses_the_new_provider(monkeypatch):
    original_enabled = settings.NEW_RELIC_ENABLED
    original_endpoint = settings.NEW_RELIC_OTLP_ENDPOINT

    class FakeExporter:
        def __init__(self, **kwargs):
            pass

        def export(self, spans):
            return None

        def shutdown(self):
            pass

    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
        FakeExporter,
    )
    try:
        _set("NEW_RELIC_ENABLED", True)
        _set("NEW_RELIC_OTLP_ENDPOINT", "http://localhost:4318")

        otel.shutdown_otel()  # no-op with nothing wired
        otel.shutdown_otel()

        assert otel.setup_otlp_tracing() is True
        assert otel.get_tracer("x") is not None
        otel.shutdown_otel()
        assert otel.get_tracer("x") is None
        # After shutdown the wiring can be re-done. get_tracer() reads this
        # module's provider, not the set-once OTel global, so the re-wire is
        # observable here — that is the whole reason for the module-level ref.
        assert otel.setup_otlp_tracing() is True
        assert otel.get_tracer("x") is not None
    finally:
        _set("NEW_RELIC_ENABLED", original_enabled)
        _set("NEW_RELIC_OTLP_ENDPOINT", original_endpoint)


# ── real SDK behaviour through the app's own call paths ─────────────────────


def _points_by_name(data) -> dict:
    out: dict = {}
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                for point in metric.data.data_points:
                    value = getattr(point, "value", None)
                    if value is None:
                        value = getattr(point, "sum", None)
                    out[metric.name] = value
    return out


@requires_otel
def test_metrics_collector_forwards_samples_to_otel(monkeypatch):
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    # Inject only the meter source; the collector's own forwarding path runs.
    monkeypatch.setattr(metrics_mod, "get_meter", lambda name: provider.get_meter(name))
    try:
        collector = metrics_mod.MetricsCollector()
        collector.increment("tool_calls", 3)
        collector.record_error("TimeoutError")
        collector.record_latency("chat", 250.0)

        values = _points_by_name(reader.get_metrics_data())
        assert values["nexus_requests_total"] == 3
        assert values["nexus_errors_total"] == 1
        assert abs(values["nexus_latency_seconds"] - 0.25) < 1e-6
    finally:
        provider.shutdown()


@requires_otel
def test_trace_span_records_a_real_otel_span(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing_mod, "get_tracer", lambda name: provider.get_tracer(name))

    async def _run_ok():
        async with tracing_mod.trace_span("node_planner", {"node": "planner"}):
            pass

    try:
        _await(_run_ok())
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        finished = spans[0]
        assert finished.name == "node_planner"
        assert finished.attributes["node"] == "planner"
        assert finished.attributes["status"] == "success"
    finally:
        provider.shutdown()


@requires_otel
def test_trace_span_records_an_error_span(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing_mod, "get_tracer", lambda name: provider.get_tracer(name))

    async def _run_fail():
        async with tracing_mod.trace_span("node_coder", {"node": "coder"}):
            raise ValueError("boom")

    try:
        with pytest.raises(ValueError, match="boom"):
            _await(_run_fail())
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        finished = spans[0]
        assert finished.attributes["error_type"] == "ValueError"
        assert finished.status.status_code.name == "ERROR"
    finally:
        provider.shutdown()


@requires_otel
def test_trace_span_is_unchanged_when_unwired():
    """The no-OTel path still yields attributes and never raises."""
    # The autouse fixture has already ensured get_tracer() returns None here
    # (NEW_RELIC_ENABLED is false under tests/conftest.py), so this exercises
    # the degrade-to-logging branch a normal local/dev run uses.
    assert otel.get_tracer("x") is None

    async def _run_ok():
        async with tracing_mod.trace_span("node_noop", {"k": "v"}) as data:
            assert data["k"] == "v"
            return data

    result = _await(_run_ok())
    assert result == {"k": "v"}


# ── the HTTP path is a metrics producer ─────────────────────────────────────


def test_http_request_metrics_count_errors(monkeypatch):
    """A 5xx is both a latency sample and an error sample; 2xx is not an error."""
    import backend.app.main as main_mod
    from backend.app.main import _record_http_request_metrics

    collector = metrics_mod.MetricsCollector()
    monkeypatch.setattr(main_mod, "metrics_collector", collector)

    _record_http_request_metrics(200, 12.0)
    _record_http_request_metrics(503, 30.0)

    assert collector._counts["http_requests_total"] == 2
    assert collector._latencies["http_request"] == [12.0, 30.0]
    assert collector._errors["http_503"] == 1


@pytest.mark.asyncio
async def test_the_http_middleware_records_request_metrics(monkeypatch):
    """Every request through the app feeds the collector, wired or not."""
    import backend.app.main as main_mod
    from backend.app.main import app
    from httpx import ASGITransport, AsyncClient

    collector = metrics_mod.MetricsCollector()
    # get_tracer() returns None under tests/conftest.py, so the middleware takes
    # the no-OTel branch — the one that must still record request metrics.
    monkeypatch.setattr(main_mod, "metrics_collector", collector)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/metrics")

    assert resp.status_code == 200
    assert collector._counts["http_requests_total"] >= 1
    assert collector._latencies["http_request"]
    assert "http_request" in collector.get_summary()["latency_stats"]
