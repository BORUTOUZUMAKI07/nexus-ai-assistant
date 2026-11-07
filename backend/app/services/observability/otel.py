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
import socket
from typing import Any
from urllib.parse import urlsplit

import structlog
from backend.app.core.config import settings

logger = structlog.get_logger(__name__)

# Module-level singletons, mirroring logs.py. Reset by shutdown_otel() so tests
# (and a re-entrant startup) can wire the providers again.
_tracer_provider: Any = None
_meter_provider: Any = None

# Cached result of the one-time collector host resolution (see
# `collector_endpoint_resolvable`). None means "not probed yet"; reset by
# shutdown_otel() alongside the providers.
_collector_resolved: bool | None = None


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


def collector_endpoint_resolvable() -> bool:
    """Whether the OTLP endpoint's host resolves. Probed once per process.

    A collector service that was never created — the usual first-deploy
    mistake, where the backend points at ``http://nexus-otel-collector:4318``
    but only the backend exists — is not a transient export failure the SDK
    shrugs off. Every batch is retried three times with backoff and then
    dropped, and that storm repeats on each export interval for the life of
    the process: `Transient error ... NameResolutionError` for logs, traces and
    metrics every 30s, plus a multi-second stall at shutdown while the last
    batch gives up.

    So the host is resolved once, *before* any exporter is constructed. When it
    does not resolve, all three signals stay off behind a single warning and
    startup pays one DNS lookup.

    Only the "no such service" case is caught: a collector that resolves but is
    down (or slept) is still retried by the SDK, because that is a real outage
    worth seeing. Fail-open, like every other telemetry seam here — a probe bug
    must never break startup.
    """
    global _collector_resolved
    if _collector_resolved is not None:
        return _collector_resolved
    base = _base_endpoint()
    if base is None:
        _collector_resolved = False
        return False
    parts = urlsplit(base if "//" in base else f"//{base}")
    host = parts.hostname
    if not host:
        _collector_resolved = False
        return False
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        _collector_resolved = True
    except OSError as exc:  # socket.gaierror is an OSError
        logger.warning(
            "otlp_collector_host_unresolvable_export_disabled",
            host=host,
            port=port,
            error_type=type(exc).__name__,
            detail="create the collector service or unset NEW_RELIC_ENABLED",
        )
        _collector_resolved = False
    return _collector_resolved


# Passwords that ship in this repo or its compose file, or that nobody could
# call a secret. Their presence is not a bug — local compose uses them so that
# `docker compose up` works with zero config — but on a public receiver they
# mean the deployment was never finished.
_PLACEHOLDER_OTLP_PASSWORDS = frozenset(
    {"", "nexus", "password", "pass", "changeme", "secret", "replace_me"}
)

# Hostnames that never leave the machine or the compose network, so a weak
# password on them is only a local concern (and is what compose ships).
_LOCAL_COLLECTOR_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "host.docker.internal",
        "otel-collector",
        "collector",
    }
)

_otlp_credentials_checked = False


def warn_if_placeholder_otlp_credentials() -> None:
    """Say once, at startup, that the public receiver will answer 401 forever.

    The receiver is public by necessity (a Free Render web service cannot
    receive private-network traffic), so its only guard is HTTP basic auth. When
    the app's OTLP_AUTH_USER/OTLP_AUTH_PASSWORD disagree with the collector's
    pair, the collector rejects every push and the app keeps serving normally:
    no crash, no failed request, no telemetry — the failure is entirely silent.

    So it is stated once, in the log, at startup: the host that will reject us
    and which variables to compare. Fail-open and best-effort, like every other
    telemetry seam here — a probe bug must never break startup.
    """
    global _otlp_credentials_checked
    if _otlp_credentials_checked:
        return
    base = _base_endpoint()
    if base is None:
        return
    parts = urlsplit(base if "//" in base else f"//{base}")
    host = (parts.hostname or "").lower()
    if not host or host in _LOCAL_COLLECTOR_HOSTS:
        return
    _otlp_credentials_checked = True

    user = (settings.OTLP_AUTH_USER or "").strip()
    secret = (settings.OTLP_AUTH_PASSWORD or "").strip()
    if user and secret.lower() not in _PLACEHOLDER_OTLP_PASSWORDS and len(secret) >= 8:
        return
    logger.warning(
        "otlp_credentials_look_like_placeholder",
        host=host,
        has_user=bool(user),
        password_length=len(secret),
        detail=(
            "the collector's public OTLP receiver answers 401 to this pair, so "
            "no logs, traces or metrics will arrive; set the same "
            "OTLP_AUTH_USER/OTLP_AUTH_PASSWORD on this service and on the "
            "collector"
        ),
    )


def _enabled() -> bool:
    return (
        bool(settings.NEW_RELIC_ENABLED)
        and _base_endpoint() is not None
        and collector_endpoint_resolvable()
    )


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
    global _tracer_provider, _meter_provider, _collector_resolved
    global _otlp_credentials_checked
    tracer_provider = _tracer_provider
    meter_provider = _meter_provider
    _tracer_provider = None
    _meter_provider = None
    _collector_resolved = None
    _otlp_credentials_checked = False
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
