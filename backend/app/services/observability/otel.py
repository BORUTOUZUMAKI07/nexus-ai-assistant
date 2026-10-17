"""
OTLP traces + metrics export — Layer-1 → Layer-2 transport (env-gated).

This is the sibling of `logs.py`: together they push all three signals
(logs, metrics, traces) to the OpenTelemetry Collector, which owns the New
Relic ingest key and forwards them on. The app never holds vendor credentials.

Why this exists alongside LangSmith: LangSmith answers "why did the agent
produce that answer?" (node I/O, prompts, tokens) and is LLM/agent-shaped.
This pipeline answers the infra/ops question — request latency, throughput,
error rates — and is what a service map / APM tool consumes. They are
complementary, not redundant.

Design rules, identical to `logs.py` so the two stay predictable:

* Strict no-op unless ``NEW_RELIC_ENABLED`` and ``NEW_RELIC_OTLP_ENDPOINT``
  are both set. With no collector, no exporter is constructed.
* When the optional ``[observability]`` extras are missing, degrade to one
  logged warning — never raise at import or startup.
* ``get_tracer`` / ``get_meter`` return ``None`` while unwired, so call sites
  guard and pay nothing when telemetry is off (the common local/test case).
* ``shutdown_otel`` is idempotent and flushes both providers.
"""
from __future__ import annotations

import base64
from typing import Any

import structlog
from backend.app.core.config import settings

logger = structlog.get_logger(__name__)

# Module-level singletons, mirroring logs.py. Reset by shutdown_otel() so tests
# (and a re-entrant startup) can wire the providers again.
_tracer_provider: Any = None
_meter_provider: Any = None


def _base_endpoint() -> str | None:
    """Collector OTLP/HTTP base URL, or None when unwired."""
    base = (settings.NEW_RELIC_OTLP_ENDPOINT or "").strip().rstrip("/")
    return base or None


def _signal_endpoint(signal: str) -> str | None:
    base = _base_endpoint()
    return None if base is None else f"{base}/v1/{signal}"


def otlp_auth_headers() -> dict[str, str] | None:
    """Basic-auth header for the collector's (public) OTLP receiver.

    Returns None when no user is configured, which is the correct shape for a
    collector that does not require auth. Shared with `logs.py` so all three
    signals authenticate identically.
    """
    user = (settings.OTLP_AUTH_USER or "").strip()
    if not user:
        return None
    secret = settings.OTLP_AUTH_PASSWORD or ""
    token = base64.b64encode(f"{user}:{secret}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _resource() -> Any:
    """The OTel Resource every signal is tagged with.

    `service.version` matters because the collector upserts it too — carrying it
    app-side means a trace/metric is identifiable by release even if a signal
    reaches a sink directly.
    """
    from opentelemetry.sdk.resources import Resource

    return Resource.create(
        {
            "service.name": settings.PROJECT_NAME,
            "service.version": settings.SERVICE_VERSION,
            "deployment.environment": settings.ENVIRONMENT,
        }
    )


def _enabled() -> bool:
    return bool(settings.NEW_RELIC_ENABLED) and _base_endpoint() is not None


def setup_otlp_tracing() -> bool:
    """Wire a global TracerProvider → OTLP span exporter (once).

    Returns True when a provider was (or already is) wired. A strict no-op when
    telemetry is off, and fail-open — a misconfiguration logs a warning and
    leaves the app on the no-op tracer rather than breaking startup.
    """
    global _tracer_provider
    if _tracer_provider is not None:
        return True
    if not _enabled():
        return False
    endpoint = _signal_endpoint("traces")
    if endpoint is None:  # defensive: _enabled() already checked the endpoint
        logger.warning("otlp_trace_endpoint_missing")
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            headers=otlp_auth_headers(),
            timeout=10,
        )
        provider = TracerProvider(resource=_resource())
        provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                schedule_delay_millis=settings.NEW_RELIC_EXPORT_INTERVAL_SECONDS * 1000,
            )
        )
        trace.set_tracer_provider(provider)
        _tracer_provider = provider
        logger.info("otlp_trace_provider_wired", endpoint=endpoint)
        return True
    except Exception as exc:  # noqa: BLE001 - fail-open seam (mirrors logs.py)
        logger.warning(
            "otlp_trace_provider_wire_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return False


def setup_otlp_metrics() -> bool:
    """Wire a global MeterProvider → OTLP metric exporter (once).

    Same gating and fail-open discipline as tracing. Metrics are pushed rather
    than scraped so multiple workers aggregate instead of each reporting a
    fraction of the truth.
    """
    global _meter_provider
    if _meter_provider is not None:
        return True
    if not _enabled():
        return False
    endpoint = _signal_endpoint("metrics")
    if endpoint is None:  # defensive: _enabled() already checked the endpoint
        logger.warning("otlp_metric_endpoint_missing")
        return False
    try:
        from opentelemetry import metrics
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        exporter = OTLPMetricExporter(
            endpoint=endpoint,
            headers=otlp_auth_headers(),
            timeout=10,
        )
        reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=settings.NEW_RELIC_EXPORT_INTERVAL_SECONDS * 1000,
        )
        provider = MeterProvider(resource=_resource(), metric_readers=[reader])
        metrics.set_meter_provider(provider)
        _meter_provider = provider
        logger.info("otlp_meter_provider_wired", endpoint=endpoint)
        return True
    except Exception as exc:  # noqa: BLE001 - fail-open seam (mirrors logs.py)
        logger.warning(
            "otlp_meter_provider_wire_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return False


def get_tracer(name: str) -> Any | None:
    """A tracer when tracing is wired, else None (callers guard, so it is free).

    Resolved from this module's provider rather than the OTel global: the global
    is set-once per process, so a re-wire (tests, or a restart within one
    process) would otherwise hand out a tracer bound to the previous, already
    shut-down provider.
    """
    if _tracer_provider is None:
        return None
    return _tracer_provider.get_tracer(name)


def get_meter(name: str) -> Any | None:
    """A meter when metrics are wired, else None (same rationale as get_tracer)."""
    if _meter_provider is None:
        return None
    return _meter_provider.get_meter(name)


def shutdown_otel() -> None:
    """Flush and shut down both providers.

    Idempotent and safe to call when nothing was ever wired. Flushing here is
    what stops the last batch of spans/metrics being lost on a clean shutdown.
    """
    global _tracer_provider, _meter_provider
    tracer_provider = _tracer_provider
    meter_provider = _meter_provider
    _tracer_provider = None
    _meter_provider = None
    _shutdown_provider("otlp_trace_provider_shutdown_failed", tracer_provider)
    _shutdown_provider("otlp_meter_provider_shutdown_failed", meter_provider)


def _shutdown_provider(event: str, provider: Any) -> None:
    if provider is None:
        return
    try:
        provider.force_flush()
        provider.shutdown()
    except Exception as exc:  # noqa: BLE001 - teardown must never break shutdown
        logger.warning(event, error_type=type(exc).__name__)
