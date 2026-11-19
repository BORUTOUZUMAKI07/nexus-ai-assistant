"""One startup probe per telemetry backend, with a read-back where possible.

New Relic receives an OpenTelemetry ``app_startup`` span and we check that the
provider handed it to the exporter. LangSmith receives a real run and we read
that run back by id — the only locally verifiable proof that it arrived. These
checks are bounded, happen once per process, and never block or break startup.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import langsmith
from backend.app.core.config import settings
from backend.app.core.logging import get_logger
from backend.app.services.observability.otel import force_flush_traces
from backend.app.services.observability.tracing import trace_span

logger = get_logger("nexus.startup_telemetry")

_STATUS: dict[str, dict[str, Any]] = {
    "new_relic": {"status": "pending"},
    "langsmith": {"status": "pending"},
}


def get_startup_telemetry_status() -> dict[str, dict[str, Any]]:
    """A copy, so a caller cannot mutate the process-wide verification result."""
    return {name: dict(value) for name, value in _STATUS.items()}


async def _verify_new_relic() -> dict[str, Any]:
    if not settings.NEW_RELIC_ENABLED:
        return {"status": "not_configured"}
    async with trace_span("app_startup", {"phase": "startup", "purpose": "delivery_probe"}):
        pass
    timeout_ms = int(settings.STARTUP_TELEMETRY_TIMEOUT_SECONDS * 1000)
    flushed = await asyncio.to_thread(force_flush_traces, timeout_ms)
    if not flushed:
        logger.warning("new_relic_startup_span_flush_failed")
        return {"status": "degraded", "detail": "flush_failed"}
    return {"status": "ok", "span": "app_startup", "detail": "span_flushed"}


def _langsmith_roundtrip() -> bool:
    """Synchronous SDK work, run in a worker thread by the caller."""
    client = langsmith.Client(
        api_key=settings.LANGSMITH_API_KEY,
        api_url=settings.LANGSMITH_ENDPOINT,
        auto_batch_tracing=False,
        timeout_ms=int(settings.STARTUP_TELEMETRY_TIMEOUT_SECONDS * 1000),
    )
    run_id = uuid4()
    now = datetime.now(timezone.utc)
    client.create_run(
        id=run_id,
        name="nexus_startup_delivery_probe",
        inputs={"purpose": "startup_delivery_check"},
        run_type="chain",
        project_name=settings.LANGSMITH_PROJECT,
        start_time=now,
        end_time=now,
    )
    return client.read_run(run_id) is not None


async def _verify_langsmith() -> dict[str, Any]:
    if not (settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY):
        return {"status": "not_configured"}
    try:
        delivered = await asyncio.wait_for(
            asyncio.to_thread(_langsmith_roundtrip),
            timeout=settings.STARTUP_TELEMETRY_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        return {"status": "degraded", "detail": "timeout"}
    except Exception as exc:  # noqa: BLE001 - telemetry must never break startup
        logger.warning("langsmith_startup_probe_failed", error_type=type(exc).__name__)
        return {"status": "degraded", "detail": type(exc).__name__}
    if not delivered:
        return {"status": "degraded", "detail": "roundtrip_failed"}
    return {"status": "ok", "detail": "run_read_back"}


async def verify_startup_telemetry() -> None:
    """Run once in the background after startup; update the public status."""
    try:
        _STATUS["new_relic"] = await _verify_new_relic()
        _STATUS["langsmith"] = await _verify_langsmith()
    except Exception as exc:  # noqa: BLE001 - fail open, like every telemetry seam
        logger.warning("startup_telemetry_probe_error", error_type=type(exc).__name__)
