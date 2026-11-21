"""
Trace store for side-by-side trace comparison.

Reads persisted evaluation logs (``evaluation_logs``) by ``trace_id`` and
projects the metric rows into the JSON shape consumed by the admin trace
endpoints. Evaluation is the only subsystem that already persists a stable
trace id, so this gives the comparison UI real stored data without adding a
second tracing backend. Reads fail open: a store outage yields ``None`` (the
route answers 404) instead of taking down the admin page.
"""
from __future__ import annotations

from typing import Any

import structlog
from backend.app.domain.usage.models import EvaluationLog
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


def _as_number(value: Any, default: float = 0.0) -> float:
    """Coerce a persisted score/metadata value to a float for delta maths."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def get_trace_span(trace_id: str, session: AsyncSession) -> dict[str, Any] | None:
    """Return a trace span built from evaluation logs, or ``None`` if absent.

    The read is deliberately best-effort. The admin viewer is an observability
    aid, not a critical path, so a missing table or a transient database error
    degrades to "trace not found" rather than raising.
    """
    if not trace_id:
        return None
    try:
        result = await session.exec(
            select(EvaluationLog)
            .where(EvaluationLog.trace_id == trace_id)
            .order_by(EvaluationLog.created_at)
        )
        rows = result.all()
    except Exception as exc:
        logger.warning("trace_store_read_failed", trace_id=trace_id, error=str(exc))
        return None

    if not rows:
        return None

    events: list[dict[str, Any]] = []
    metrics: dict[str, float] = {}
    metadata: dict[str, Any] = {}
    for row in rows:
        events.append(
            {
                "metric": row.metric_name,
                "score": _as_number(row.score),
                "passed": bool(row.passed),
                "reason": row.reason,
                "evaluator": row.evaluator,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
        metrics[row.metric_name.strip().lower()] = _as_number(row.score)
        if isinstance(row.metadata_json, dict):
            metadata.update(row.metadata_json)

    def metric(*names: str) -> float:
        for name in names:
            if name in metrics:
                return metrics[name]
        return 0.0

    first = rows[0]
    return {
        "trace_id": trace_id,
        "conversation_id": str(first.conversation_id) if first.conversation_id else None,
        "message_id": str(first.message_id) if first.message_id else None,
        "evaluator": first.evaluator,
        "created_at": first.created_at.isoformat() if first.created_at else None,
        "events": events,
        "model": metadata.get("model") or metrics.get("model"),
        "cost_usd": _as_number(metadata.get("cost_usd", metric("cost_usd", "cost"))),
        "tokens": _as_number(metadata.get("tokens", metric("tokens", "total_tokens"))),
        "latency_ms": _as_number(metadata.get("latency_ms", metric("latency_ms", "latency"))),
        "confidence_score": _as_number(
            metadata.get("confidence_score", metric("confidence_score", "confidence"))
        ),
    }
