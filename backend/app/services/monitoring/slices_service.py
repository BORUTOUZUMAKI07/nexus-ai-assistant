"""
Popularity-Bucketed Slice Monitoring + Fairness Surface.

``SliceService`` aggregates production telemetry into per-(model, provider)
slices with volume, error rate, latency, cost, and user-feedback helpful rate,
then ranks slices by popularity (request volume) and flags the *high-volume
slices whose helpful rate undercuts the overall rate* — the anomaly signal
popularity-bucketed monitoring exists to surface. ``fairness_report`` adds an
explicit, deliberately-slim parity surface (eval pass-rate parity by evaluator
and model + provider error parity) with a documented limitations block.

All aggregation is column-select + in-Python grouping so it runs identically
against the fake session in tests and Postgres in production.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from backend.app.domain.conversation.models import Message
from backend.app.domain.usage.models import EvaluationLog, UsageLog
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)

_HELPFUL_FEEDBACK = {"thumbs_up", "thumbs_down"}
_POPULAR_TOP_FRACTION = 0.4          # top 40% by volume counts as "popular"
_HELPFUL_GAP = 0.10                  # flag when popular slice lags overall helpful rate by >10pp
_ERROR_RATE_FLOOR = 0.15             # provider error-rate parity flag threshold


@dataclass
class _UsageSlice:
    model: str
    provider: str
    volume: int = 0
    errors: int = 0
    latency: list[float] = field(default_factory=list)
    cost: float = 0.0

    @property
    def error_rate(self) -> float:
        return self.errors / self.volume if self.volume else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return sum(self.latency) / len(self.latency) if self.latency else 0.0


class SliceService:
    """Computes popularity-bucketed slice reports + fairness parity over telemetry."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def slice_report(self) -> dict[str, Any]:
        """Per-model/provider slices ranked by popularity with quality flags."""
        slices: dict[tuple[str, str], _UsageSlice] = {}
        usage_rows = (await self._session.exec(select(UsageLog))).all()
        for row in usage_rows:
            model = row.model or "unknown"
            provider = row.provider or "unknown"
            key = (model, provider)
            sl = slices.setdefault(key, _UsageSlice(model=model, provider=provider))
            sl.volume += 1
            if row.status not in (None, "success"):
                sl.errors += 1
            sl.latency.append(float(row.latency_ms or 0.0))
            sl.cost += float(row.cost_usd or 0.0)

        # Feedback helpful rates per model (message-level thumbs).
        helpful: dict[str, list[int]] = {}
        msg_rows = (await self._session.exec(select(Message))).all()
        for row in msg_rows:
            feedback = row.user_feedback
            if feedback in _HELPFUL_FEEDBACK:
                bucket = helpful.setdefault(row.model or "unknown", [])
                bucket.append(1 if feedback == "thumbs_up" else 0)

        overall_helpful = _mean_of_all([v for vs in helpful.values() for v in vs])
        total_volume = sum(sl.volume for sl in slices.values())
        total_errors = sum(sl.errors for sl in slices.values())
        overall_helpful_flag = overall_helpful is not None

        ranked = sorted(slices.values(), key=lambda s: s.volume, reverse=True)
        slice_rows: list[dict[str, Any]] = []
        popularity_flags: list[dict[str, Any]] = []
        for rank, sl in enumerate(ranked):
            popularity_bucket = _popularity_bucket(rank, len(ranked))
            helpful_rate = (
                round(sum(helpful.get(sl.model, [])) / len(helpful[sl.model]), 4)
                if helpful.get(sl.model)
                else None
            )
            flags: list[str] = []
            if (
                popularity_bucket == "popular"
                and overall_helpful_flag
                and helpful_rate is not None
                and overall_helpful is not None
                and helpful_rate < overall_helpful - _HELPFUL_GAP
            ):
                flags.append("high_popularity_low_quality")
            slice_rows.append(
                {
                    "model": sl.model,
                    "provider": sl.provider,
                    "volume": sl.volume,
                    "rank": rank + 1,
                    "popularity_bucket": popularity_bucket,
                    "error_rate": round(sl.error_rate, 4),
                    "avg_latency_ms": round(sl.avg_latency_ms, 1),
                    "cost_usd": round(sl.cost, 4),
                    "helpful_rate": helpful_rate,
                    "flags": flags,
                }
            )
            if flags:
                popularity_flags.append(
                    {"slice": f"{sl.model} / {sl.provider}", "helpful_rate": helpful_rate, "flags": flags}
                )

        logger.info("slice_report_computed", slices=len(slice_rows), popularity_flags=len(popularity_flags))
        return {
            "overall": {
                "requests": total_volume,
                "error_rate": round(total_errors / total_volume, 4) if total_volume else 0.0,
                "helpful_rate": round(overall_helpful, 4) if overall_helpful is not None else None,
                "avg_latency_ms": round(sum(sl.avg_latency_ms for sl in slices.values()) / len(slices), 1) if slices else 0.0,
                "combined_cost_usd": round(sum(sl.cost for sl in slices.values()), 4),
            },
            "slices": slice_rows,
            "popularity_flags": popularity_flags,
        }

    async def fairness_report(self) -> dict[str, Any]:
        """Slim parity surface: eval pass-rate parity + provider error parity."""
        eval_groups: dict[tuple[str, str], dict[str, int]] = {}
        eval_rows = (await self._session.exec(select(EvaluationLog))).all()
        for row in eval_rows:
            key = (row.evaluator or "unknown", row.metric_name or "unknown")
            group = eval_groups.setdefault(key, {"count": 0, "passed": 0})
            group["count"] += 1
            if row.passed:
                group["passed"] += 1

        model_groups: dict[str, dict[str, int]] = {}
        for row in eval_rows:
            model = (row.metadata_json or {}).get("model")
            if not model:
                continue
            group = model_groups.setdefault(str(model), {"count": 0, "passed": 0})
            group["count"] += 1
            if row.passed:
                group["passed"] += 1

        usage_groups: dict[str, dict[str, int]] = {}
        usage_rows = (await self._session.exec(select(UsageLog))).all()
        for row in usage_rows:
            group = usage_groups.setdefault(row.provider or "unknown", {"count": 0, "errors": 0})
            group["count"] += 1
            if row.status not in (None, "success"):
                group["errors"] += 1

        return {
            "evaluator_parity": [
                {"group": f"{ev} / {metric}", "count": g["count"], "pass_rate": round(g["passed"] / g["count"], 4)}
                for (ev, metric), g in sorted(eval_groups.items())
            ],
            "model_parity": [
                {"model": model, "count": g["count"], "pass_rate": round(g["passed"] / g["count"], 4)}
                for model, g in sorted(model_groups.items())
            ],
            "provider_error_parity": [
                {
                    "provider": provider,
                    "count": g["count"],
                    "error_rate": round(g["errors"] / g["count"], 4) if g["count"] else 0.0,
                    "flagged": g["count"] > 0 and g["errors"] / g["count"] > _ERROR_RATE_FLOOR,
                }
                for provider, g in sorted(usage_groups.items())
            ],
            "limitations": (
                "Fairness surface is deliberately slim (population-level parity only). "
                "Protected-attribute cohorts are not collected, so statistical-parity "
                "claims are out of scope; treat these numbers as an early-warning "
                "signal, not an audit conclusion. Stanford AI Index 2026: fairness "
                "metrics themselves are still immature across the industry."
            ),
        }


def _mean_of_all(values: list[int]) -> float | None:
    return sum(values) / len(values) if values else None


def _popularity_bucket(rank: int, total: int) -> str:
    if total == 0:
        return "empty"
    position = (rank + 1) / total
    if position <= _POPULAR_TOP_FRACTION:
        return "popular"
    if position <= _POPULAR_TOP_FRACTION + (1.0 - _POPULAR_TOP_FRACTION) / 2:
        return "moderate"
    return "long_tail"
