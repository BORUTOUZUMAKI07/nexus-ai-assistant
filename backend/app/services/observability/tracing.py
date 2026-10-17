"""
Execution Tracing & Span Context Managers.
Provides distributed trace contexts for LangGraph nodes and tool dispatchers.

The spans are real OpenTelemetry spans when the OTLP pipeline is wired
(``services/observability/otel.py``, gated by ``NEW_RELIC_ENABLED``); otherwise
this degrades to the in-process timing/logging it always did, so callers are
unaffected when observability is off. LangSmith remains the LLM/agent-level
trace view; these spans are the infra/ops view (request → node timings).
"""
import time
from collections.abc import AsyncGenerator
from contextlib import ExitStack, asynccontextmanager
from typing import Any

import structlog
from backend.app.services.observability.metrics import metrics_collector
from backend.app.services.observability.otel import get_tracer

logger = structlog.get_logger(__name__)

_TRACER_NAME = "backend.app"


def _set_span_attributes(span: Any, attributes: dict[str, Any]) -> None:
    """Best-effort attribute tagging — never let a bad value break a span."""
    for key, value in attributes.items():
        try:
            if isinstance(value, (str, bool, int, float)):
                span.set_attribute(key, value)
            else:
                span.set_attribute(key, str(value))
        except Exception:  # noqa: BLE001 - observability must not raise
            logger.debug("span_attribute_failed", key=key)
            continue


@asynccontextmanager
async def trace_span(
    span_name: str,
    attributes: dict[str, Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Async context manager that measures duration, logs spans, and tracks error telemetry.

    When the OTLP pipeline is wired the same work is recorded as an
    OpenTelemetry span nested under the ambient context; when it is not, the
    context manager is unchanged from before (logging + in-process metrics).
    """
    span_data: dict[str, Any] = attributes.copy() if attributes else {}
    start_time = time.perf_counter()
    logger.info(f"span_start_{span_name}", **span_data)

    tracer = get_tracer(_TRACER_NAME)
    with ExitStack() as stack:
        span: Any = None
        if tracer is not None:
            span = stack.enter_context(tracer.start_as_current_span(span_name))
            _set_span_attributes(span, span_data)

        try:
            yield span_data
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
            if span is not None:
                # The span context manager also ends the span on the way out;
                # recording here keeps the span self-describing even when a
                # caller later swallows the exception.
                _set_span_attributes(
                    span,
                    {
                        "duration_ms": round(duration_ms, 2),
                        "error_type": type(exc).__name__,
                    },
                )
                span.record_exception(exc)
            raise
        else:
            duration_ms = (time.perf_counter() - start_time) * 1000
            metrics_collector.record_latency(span_name, duration_ms)
            logger.info(
                f"span_finish_{span_name}",
                duration_ms=round(duration_ms, 2),
                status="success",
                **span_data,
            )
            if span is not None:
                _set_span_attributes(
                    span,
                    {"duration_ms": round(duration_ms, 2), "status": "success"},
                )
