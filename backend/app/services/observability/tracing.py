"""
Execution Tracing & Span Context Managers.
Provides distributed trace contexts for LangGraph nodes and tool dispatchers.
"""
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from backend.app.services.observability.metrics import metrics_collector

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def trace_span(
    span_name: str,
    attributes: dict[str, Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Async context manager that measures duration, logs spans, and tracks error telemetry."""
    span_data: dict[str, Any] = attributes.copy() if attributes else {}
    start_time = time.perf_counter()
    logger.info(f"span_start_{span_name}", **span_data)

    try:
        yield span_data
        duration_ms = (time.perf_counter() - start_time) * 1000
        metrics_collector.record_latency(span_name, duration_ms)
        logger.info(
            f"span_finish_{span_name}",
            duration_ms=round(duration_ms, 2),
            status="success",
            **span_data,
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - start_time) * 1000
        metrics_collector.record_error(f"{span_name}_{type(exc).__name__}")
        logger.error(
            f"span_error_{span_name}",
            duration_ms=round(duration_ms, 2),
            error=str(exc),
            error_type=type(exc).__name__,
            **span_data,
        )
        raise exc
