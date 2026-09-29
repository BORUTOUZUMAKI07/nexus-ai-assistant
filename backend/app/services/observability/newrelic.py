"""
New Relic OTLP Metrics Bridge (optional, env-gated).

Mirrors the in-process ``MetricsCollector`` (the zero-dependency source of
truth behind ``/metrics`` and the admin viewer) into New Relic over OTLP/HTTP
so the free tier can render hosted dashboards + alerts without touching any
runtime code path.

Deliberately safe (same fail-open discipline as the rest of the stack):

* When ``NEW_RELIC_ENABLED=false`` or ``NEW_RELIC_LICENSE_KEY`` is unset this
  module is a strict no-op — every entry point returns ``None``.
* When enabled but the optional ``[observability]`` extras (``opentelemetry-sdk``
  + ``opentelemetry-exporter-otlp-proto-http``) are not installed it degrades
  to a logged warning, never an exception.
* The in-process collector stays authoritative; NR is a hosted *sink* only.

The exporter pulls readings straight from ``metrics_collector.get_summary()``
on each OTLP export interval via observable instruments, so anything the local
Prometheus endpoint exposes is mirrored 1:1 (same labels, same semantics):
counters as counters, error spans as counters, latency percentiles as gauges.

Observable callbacks in this opentelemetry-sdk line return
``Iterable[Observation]`` (see ``CallbackT``) rather than pushing through an
observer object; the class is imported lazily and cached so the module
imports cleanly without the optional extras installed.
"""
from __future__ import annotations

import importlib.util
from typing import Any, Callable

import structlog
from backend.app.core.config import settings
from backend.app.services.observability.metrics import metrics_collector

logger = structlog.get_logger(__name__)

# Lazily-bound at first callback invocation (only ever runs on the OTel export
# thread when the extras are installed); keeps module import dependency-free.
_observation_cls: type | None = None


def _make_observation(value: int | float, attributes: dict[str, str]) -> Any:
    global _observation_cls
    if _observation_cls is None:
        from opentelemetry.metrics import Observation  # type: ignore[import-not-found]

        _observation_cls = Observation
    return _observation_cls(value, attributes)


def _observe_requests(_options: Any) -> list[Any]:
    """Snapshot counters → OTel Observations (cumulative; SDK computes deltas)."""
    return [
        _make_observation(value, {"operation": name})
        for name, value in metrics_collector.get_summary()["counters"].items()
    ]


def _observe_errors(_options: Any) -> list[Any]:
    """Snapshot error spans → OTel Observations (cumulative; SDK computes deltas)."""
    return [
        _make_observation(value, {"error": name})
        for name, value in metrics_collector.get_summary()["errors"].items()
    ]


def _observe_latency(_options: Any) -> list[Any]:
    """Snapshot per-operation latency percentiles (ms → seconds) as gauges."""
    summary = metrics_collector.get_summary()
    observations: list[Any] = []
    for op, stats in summary["latency_stats"].items():
        for quantile, key in (("avg", "avg_ms"), ("p50", "p50_ms"), ("p95", "p95_ms"), ("p99", "p99_ms")):
            observations.append(
                _make_observation(float(stats[key]) / 1000.0, {"operation": op, "quantile": quantile})
            )
    return observations


def _new_relic_ready() -> bool:
    """True only when the bridge is enabled, keyed, and the extras are installed."""
    if not (settings.NEW_RELIC_ENABLED and settings.NEW_RELIC_LICENSE_KEY):
        return False
    if importlib.util.find_spec("opentelemetry") is None:
        logger.warning("newrelic_opentelemetry_missing_disabled")
        return False
    return True


def _build_exporter_and_provider() -> Any | None:
    """
    Lazily build the OTel MeterProvider wired to New Relic's OTLP/HTTP endpoint.

    Returns the provider (used as a shutdown handle), or None when the backend
    imports fail. Import errors are logged and swallowed — the bridge is a
    best-effort sink and must never take the app down.
    """
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (  # type: ignore[import-not-found]
        OTLPMetricExporter,
    )
    from opentelemetry.metrics import get_meter  # type: ignore[import-not-found]
    from opentelemetry.sdk.metrics import MeterProvider  # type: ignore[import-not-found]
    from opentelemetry.sdk.metrics.export import (  # type: ignore[import-not-found]
        PeriodicExportingMetricReader,
    )

    endpoint = settings.NEW_RELIC_OTLP_ENDPOINT
    if not endpoint.endswith("/v1/metrics"):
        endpoint = endpoint.rstrip("/") + "/v1/metrics"
    exporter = OTLPMetricExporter(
        endpoint=endpoint,
        headers={"api-key": settings.NEW_RELIC_LICENSE_KEY or ""},  # nosec B105 — ingest key for NR, not a password
        timeout=10,
    )
    reader = PeriodicExportingMetricReader(
        exporter,
        export_interval_millis=max(settings.NEW_RELIC_EXPORT_INTERVAL_SECONDS, 1) * 1000,
    )
    provider = MeterProvider(metric_readers=[reader])

    meter = get_meter("nexus-ai-assistant", version="0.1.0", meter_provider=provider)
    meter.create_observable_counter(
        "nexus_requests_total",
        description="Requests recorded by the metrics collector.",
        unit="1",
        callbacks=[_observe_requests],
    )
    meter.create_observable_counter(
        "nexus_errors_total",
        description="Errors recorded by exception spans / gateways.",
        unit="1",
        callbacks=[_observe_errors],
    )
    meter.create_observable_gauge(
        "nexus_latency_seconds",
        description="Latency percentiles per operation (seconds).",
        unit="s",
        callbacks=[_observe_latency],
    )
    return provider


def start_new_relic_export() -> Callable[[], None] | None:
    """Start the New Relic OTLP bridge.

    Returns a ``shutdown`` callable when the bridge is live, else ``None``
    (disabled / unkeyed / extras missing). The returned callable flushes and
    stops the periodic exporter; it is safe to call multiple times.
    """
    if not _new_relic_ready():
        return None
    try:
        provider = _build_exporter_and_provider()
    except Exception as exc:  # fail-open: a broken NR setup must not break the app
        logger.warning("newrelic_bridge_start_failed", error=str(exc))
        return None

    def _shutdown() -> None:
        try:
            provider.shutdown()
        except Exception as exc:
            logger.warning("newrelic_bridge_shutdown_failed", error=str(exc))

    logger.info(
        "newrelic_bridge_started",
        endpoint=settings.NEW_RELIC_OTLP_ENDPOINT,
        interval_s=settings.NEW_RELIC_EXPORT_INTERVAL_SECONDS,
    )
    return _shutdown
