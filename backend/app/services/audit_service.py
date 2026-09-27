"""
Responsible-ML / Compliance Audit Surface (MD §8.7 → validated, EU AI Act).

Aggregates the evidence a downstream AI provider needs for its responsibility
story into one admin read model: which system controls are actually on
(2FA/redaction/guardrails/hooks), model + prompt provenance over recent usage,
the latest red-team battery result, GDPR export/erasure evidence, the eval
histogram, and an explicit EU AI Act classification statement (with its
effective dates) rather than a hand-wavy "we are compliant".

Every query is a column select + Python grouping, so the service runs against
the fake session in tests and Postgres in production.
"""
from __future__ import annotations

from typing import Any

import structlog
from backend.app.core.config import settings
from backend.app.domain.hook.models import HookPolicy
from backend.app.domain.prompt.models import PromptVersion
from backend.app.domain.redteam.models import RedTeamRun
from backend.app.domain.system.models import AuditLog
from backend.app.domain.usage.models import EvaluationLog, UsageLog
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)

_GDPR_ACTIONS = ("gdpr_export", "gdpr_erasure")


def eu_ai_act_classification() -> dict[str, Any]:
    """
    Static classification statement. Nexus is a *downstream* provider of a
    general-purpose chat interface (not a foundation-model provider), so the
    Art 51-55 high-risk framework does not apply to us directly; the chatbot is
    best read as limited-risk / Art 50 transparency, and any GPAI models we
    call are the responsibility of their upstream providers (Art 53 obligations
    have applied since 2025-08-02 to models placed on the EU market).
    """
    return {
        "classification": "downstream provider of a general-purpose chatbot",
        "high_risk_articles": "Not directly subject to Art 51-55 (not a high-risk deployer under Annex III for this interface)",
        "transparency_obligations": {
            "art_50": "Limited-risk chatbot transparency obligations apply since 2026-08-02",
            "disclosure": "Users should be disclosed that they interact with an AI system",
        },
        "gpaI_models": {
            "role": "Downstream provider consuming GPAI models from upstream providers",
            "upstream_obligations": "Art 53 GPAI obligations have applied since 2025-08-02; on-market GPAI models must comply by 2027-08-02",
        },
        "fines": "Up to EUR 15M or 3% of global annual turnover for non-compliance with applicable obligations",
        "internal_evidence": {
            "iso_iec_42001": "Documented management-system evidence is reusable as control evidence but is NOT Art 17-equivalent",
            "status_date": "2026-09-27",
        },
    }


def retention_statement() -> str:
    return (
        "Chat data is retained for the active account lifecycle and erased on "
        "right-to-erasure; logs keep audit metadata per industry norms. "
        f"Response cache TTL: {settings.RESPONSE_CACHE_TTL_SECONDS}s "
        f"({'enabled' if settings.RESPONSE_CACHE_ENABLED else 'disabled'})."
    )


class AuditService:
    """Read model for the compliance/audit admin surface."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def _controls(self) -> dict[str, Any]:
        return {
            "pii_redaction_enabled": settings.PII_REDACTION_ENABLED,
            "response_cache_enabled": settings.RESPONSE_CACHE_ENABLED,
            "rate_limit_per_minute": settings.RATE_LIMIT_PER_MINUTE,
            "totp_available": bool(settings.TOTP_ISSUER),
        }

    async def report(self) -> dict[str, Any]:
        """Aggregate the compliance evidence snapshot (fail-open per section)."""
        sections: dict[str, Any] = {}

        # System controls
        sections["controls"] = self._controls
        try:
            hook_policies = (await self._session.exec(select(HookPolicy))).all()
            sections["lifecycle_hooks"] = {
                "policy_count": len(hook_policies),
                "enabled": sum(1 for h in hook_policies if (h.enabled and h.action == "block")),
                "block_policies": sum(1 for h in hook_policies if h.action == "block"),
            }
        except Exception as exc:
            logger.warning("audit_lifecycle_hooks_failed", error=str(exc))
            sections["lifecycle_hooks"] = {"error": str(exc)}

        # Model provenance — last 24h usage by model + variant coverage.
        try:
            usage = (await self._session.exec(select(UsageLog))).all()
            by_model: dict[str, dict[str, Any]] = {}
            for row in usage:
                bucket = by_model.setdefault(row.model or "unknown", {"count": 0, "providers": set(), "variants": set()})
                bucket["count"] += 1
                if row.provider:
                    bucket["providers"].add(row.provider)
                variant = (row.metadata_json or {}).get("prompt_variant")
                if variant and variant != "default":
                    bucket["variants"].add(str(variant))
            sections["model_provenance"] = [
                {
                    "model": model,
                    "requests": bucket["count"],
                    "providers": sorted(bucket["providers"]),
                    "experiment_variants_seen": sorted(bucket["variants"]),
                }
                for model, bucket in sorted(by_model.items())
            ]
        except Exception as exc:
            logger.warning("audit_model_provenance_failed", error=str(exc))
            sections["model_provenance"] = {"error": str(exc)}

        # Prompt provenance
        try:
            versions = (await self._session.exec(select(PromptVersion))).all()
            sections["prompt_provenance"] = {
                "version_count": len(versions),
                "latest_timestamp": max((v.created_at.isoformat() for v in versions), default=None),
            }
        except Exception as exc:
            logger.warning("audit_prompt_provenance_failed", error=str(exc))
            sections["prompt_provenance"] = {"error": str(exc)}

        # Red-team battery (persisted runs)
        try:
            runs = (
                await self._session.exec(select(RedTeamRun).order_by(RedTeamRun.created_at.desc()))
            ).all()
            latest = runs[0] if runs else None
            sections["red_team"] = (
                {
                    "last_run_at": latest.created_at.isoformat() if latest.created_at else None,
                    "total_probes": latest.total_probes,
                    "blocked_probes": latest.blocked_probes,
                    "defense_rate": round(latest.defense_rate, 4),
                    "run_count": len(runs),
                }
                if latest
                else {"run_count": 0, "note": "No red-team run persisted yet — run /admin/evaluation/redteam"}
            )
        except Exception as exc:
            logger.warning("audit_red_team_failed", error=str(exc))
            sections["red_team"] = {"error": str(exc)}

        # GDPR evidence
        try:
            audit_rows = (await self._session.exec(select(AuditLog))).all()
            gdpr_counts: dict[str, int] = {"gdpr_export": 0, "gdpr_erasure": 0}
            for row in audit_rows:
                if row.action in _GDPR_ACTIONS:
                    gdpr_counts[row.action] += 1
            sections["gdpr"] = {key: gdpr_counts[key] for key in _GDPR_ACTIONS}
        except Exception as exc:
            logger.warning("audit_gdpr_failed", error=str(exc))
            sections["gdpr"] = {"error": str(exc)}

        # Evaluation histogram
        try:
            evals = (await self._session.exec(select(EvaluationLog))).all()
            by_evaluator: dict[str, dict[str, int]] = {}
            for row in evals:
                bucket = by_evaluator.setdefault(row.evaluator or "unknown", {"count": 0, "passed": 0})
                bucket["count"] += 1
                if row.passed:
                    bucket["passed"] += 1
            sections["evaluations"] = [
                {
                    "evaluator": evaluator,
                    "count": bucket["count"],
                    "pass_rate": round(bucket["passed"] / bucket["count"], 4) if bucket["count"] else 0.0,
                }
                for evaluator, bucket in sorted(by_evaluator.items())
            ]
        except Exception as exc:
            logger.warning("audit_evaluations_failed", error=str(exc))
            sections["evaluations"] = {"error": str(exc)}

        sections["retention"] = retention_statement()
        sections["eu_ai_act"] = eu_ai_act_classification()
        return sections

    async def redteam_runs(self, limit: int = 5) -> list[dict[str, Any]]:
        """Recent red-team runs in reverse-chronological order (audit trail)."""
        runs = (
            await self._session.exec(select(RedTeamRun).order_by(RedTeamRun.created_at.desc()))
        ).all()
        return [
            {
                "id": str(r.id),
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "total_probes": r.total_probes,
                "blocked_probes": r.blocked_probes,
                "defense_rate": round(r.defense_rate, 4),
                "probe_count": len(r.report.get("results", [])) if isinstance(r.report, dict) else 0,
            }
            for r in runs[:limit]
        ]
