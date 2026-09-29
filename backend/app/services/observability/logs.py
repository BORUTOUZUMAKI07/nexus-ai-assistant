"""
OTLP log export — Layer-1 → Layer-2 transport for structured logs (env-gated).

Every line structlog emits (through its stdlib ``LoggerFactory``) also flows to
the OpenTelemetry Collector over OTLP/HTTP; the collector owns the New Relic
ingest key and forwards everything on — the app never holds vendor credentials.

Deliberately safe (same fail-open discipline as the rest of the stack):

* Gated by ``NEW_RELIC_ENABLED``; unset (or no ``NEW_RELIC_OTLP_ENDPOINT``) is a
  strict no-op.
* When enabled but the optional ``[observability]`` extras are not installed it
  degrades to a logged warning, never an exception.
* Export problems never affect logging — a broken collector drops telemetry,
  not log lines.

``/metrics`` remains the in-process source of truth (the collector scrapes it);
traces remain LangSmith's job. This module only carries logs.
"""
from __future__ import annotations

import logging
import warnings
from typing import Any

import structlog
from backend.app.core.config import settings

logger = structlog.get_logger(__name__)

_otlp_handler: logging.Handler | None = None
_otlp_provider: Any = None


def _otlp_logs_endpoint() -> str | None:
    base = (settings.NEW_RELIC_OTLP_ENDPOINT or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/v1/logs"


def setup_otlp_log_handler(log_level: int = logging.INFO) -> logging.Handler | None:
    """Attach an OTLP log handler to the root stdlib logger (once).

    Returns the attached handler, or ``None`` when the export is disabled,
    unconfigured, or the OTel extras are missing. Calling again returns the
    already-attached handler; ``shutdown_otlp_log_handler()`` resets the state
    so wiring can be re-done (used by tests and the app shutdown path).
    """
    global _otlp_handler, _otlp_provider
    if _otlp_handler is not None:
        return _otlp_handler
    if not settings.NEW_RELIC_ENABLED:
        return None
    endpoint = _otlp_logs_endpoint()
    if endpoint is None:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource

        exporter = OTLPLogExporter(endpoint=endpoint, timeout=10)
        processor = BatchLogRecordProcessor(
            exporter,
            schedule_delay_millis=settings.NEW_RELIC_EXPORT_INTERVAL_SECONDS * 1000,
        )
        provider = LoggerProvider(
            resource=Resource.create(
                {
                    "service.name": settings.PROJECT_NAME,
                    "environment": settings.ENVIRONMENT,
                }
            )
        )
        provider.add_log_record_processor(processor)
        # The SDK's stdlib handler is deprecated in favour of the
        # opentelemetry-instrumentation-logging package; it is functionally
        # correct here, so silence the advisory warning rather than add a dep.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            handler = LoggingHandler(level=log_level, logger_provider=provider)
        logging.getLogger().addHandler(handler)
        _otlp_handler = handler
        _otlp_provider = provider
        logger.info("otlp_log_handler_wired", endpoint=endpoint)
        return handler
    except Exception as exc:  # noqa: BLE001 - fail-open seam
        logger.warning(
            "otlp_log_handler_wire_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None


def shutdown_otlp_log_handler() -> None:
    """Detach the OTLP handler and flush/shutdown the log pipeline.

    Idempotent and safe to call when nothing was ever wired.
    """
    global _otlp_handler, _otlp_provider
    handler = _otlp_handler
    provider = _otlp_provider
    _otlp_handler = None
    _otlp_provider = None
    if handler is None:
        return
    try:
        logging.getLogger().removeHandler(handler)
        handler.close()
    except Exception as exc:  # noqa: BLE001 - teardown must never break shutdown
        logger.warning("otlp_log_handler_teardown_failed", error_type=type(exc).__name__)
    if provider is not None:
        try:
            provider.force_flush()
            provider.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning("otlp_log_provider_shutdown_failed", error_type=type(exc).__name__)
