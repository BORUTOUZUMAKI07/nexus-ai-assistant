"""
Drift Service — pulls LLM-era signals from usage/evaluation telemetry and
produces sliding-window drift reports (pure math lives in drift_monitor).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from backend.app.domain.usage.models import EvaluationLog, UsageLog
from backend.app.services.monitoring.drift_monitor import rate_drift_report, summarize_drift
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)

_RECENT_USAGE_HOURS = 24
_BASELINE_USAGE_HOURS = 24
_RECENT_EVAL_DAYS = 7
_BASELINE_EVAL_DAYS = 7


class DriftService:
    """Computes sliding-window drift over production telemetry tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def collect_usage_signals(
        self,
        recent_hours: int = _RECENT_USAGE_HOURS,
        baseline_hours: int = _BASELINE_USAGE_HOURS,
    ) -> dict[str, list[float]]:
        """
        Per-request signals from usage_logs over [now-(recent+baseline), now]:
        latency_ms, cost_usd, error_rate (1.0/0.0 per row).
        """
        since = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=recent_hours + baseline_hours)
        stmt = select(UsageLog.latency_ms, UsageLog.cost_usd, UsageLog.status).where(
            UsageLog.created_at >= since
        )
        result = await self._session.exec(stmt)
        signals: dict[str, list[float]] = {
            "latency_ms": [],
            "cost_usd": [],
            "error_rate": [],
        }
        for latency_ms, cost_usd, status in result.all():  # type: ignore[misc]
            signals["latency_ms"].append(float(latency_ms or 0.0))
            signals["cost_usd"].append(float(cost_usd or 0.0))
            signals["error_rate"].append(1.0 if status not in (None, "success") else 0.0)
        return signals

    async def collect_eval_signals(
        self,
        recent_days: int = _RECENT_EVAL_DAYS,
        baseline_days: int = _BASELINE_EVAL_DAYS,
    ) -> dict[str, list[float]]:
        """
        Per-evaluation signals: faithfulness pass rate and hallucination rate
        from evaluation_logs over [now-(recent+baseline), now].
        """
        since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=recent_days + baseline_days)
        stmt = select(EvaluationLog.metric_name, EvaluationLog.passed).where(
            EvaluationLog.created_at >= since
        )
        result = await self._session.exec(stmt)
        signals: dict[str, list[float]] = {"faithfulness_pass_rate": [], "hallucination_rate": []}
        for metric_name, passed in result.all():  # type: ignore[misc]
            if metric_name == "faithfulness":
                signals["faithfulness_pass_rate"].append(1.0 if passed else 0.0)
            elif metric_name == "hallucination":
                # The metric is named for what it *detects*; a passing row = no
                # hallucination detected, so rate = fraction of failed rows.
                signals["hallucination_rate"].append(0.0 if passed else 1.0)
        return signals

    async def drift_report(self) -> dict[str, Any]:
        """Combine usage + eval signals into one drift summary (no alert noise
        when either window has insufficient samples)."""
        usage = await self.collect_usage_signals()
        evals = await self.collect_eval_signals()

        # Split each signal into its recent and immediately-preceding windows.
        recent_all: dict[str, list[float]] = {}
        baseline_all: dict[str, list[float]] = {}
        for name, series in {**usage, **evals}.items():
            half = len(series) // 2
            recent_all[name] = series[half:]
            baseline_all[name] = series[:half]

        summary = summarize_drift(recent_all, baseline_all, threshold=2.0)
        summary["window"] = {
            "usage": {"recent_hours": _RECENT_USAGE_HOURS, "baseline_hours": _BASELINE_USAGE_HOURS},
            "eval": {"recent_days": _RECENT_EVAL_DAYS, "baseline_days": _BASELINE_EVAL_DAYS},
        }

        # Per-metric detail with the window counts the summary intentionally
        # keeps flat, so the admin consumer can see data coverage.
        detail: dict[str, Any] = {}
        for name in recent_all:
            report = rate_drift_report(recent_all[name], baseline_all[name], threshold=2.0)
            report["samples_recent"] = len(recent_all[name])
            report["samples_baseline"] = len(baseline_all[name])
            detail[name] = report
        summary["detail"] = detail
        logger.info(
            "drift_report_computed",
            detected=summary["detected"],
            drifting_metrics=summary["drifting_metrics"],
        )
        return summary
