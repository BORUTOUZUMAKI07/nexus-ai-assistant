"""
Automated Prompt-Optimization Loop (MD §6.15 → valid Opik/agent-opt pattern).

One closed loop: propose K candidate rewrites of a prompt key, score the
baseline + each candidate against a golden case set with an (injectable) judge,
promote the best candidate only when it beats the baseline, and persist the
whole evidence trail (candidate scores, winner, deltas) as a
``PromptOptimizationRun``. The judge and proposer are dependencies so tests run
entirely offline against fakes; production uses the vanilla LLM judge.
"""
from __future__ import annotations

import json
import re
from statistics import mean
from typing import Any, Awaitable, Callable

import structlog
from backend.app.core.config import settings
from backend.app.domain.base_repository import BaseRepository
from backend.app.domain.optimization.models import PromptOptimizationRun
from backend.app.infrastructure.ai.litellm_client import ai_client

logger = structlog.get_logger(__name__)

Case = dict[str, Any]

Judge = Callable[[str, list[Case]], Awaitable[float]]
Proposer = Callable[[str, str, int], Awaitable[list[str]]]

_REFUSAL_HINTS = re.compile(r"\b(sorry|cannot|can't|unable|not (?:able|allowed)|refus|must not)\b", re.IGNORECASE)


def _strip_code_fence(raw: str) -> str:
    text = raw.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    return text


def _parse_candidates(raw: str) -> list[str]:
    """Best-effort extraction of the candidate list the proposer returns."""
    try:
        payload = json.loads(_strip_code_fence(raw))
    except (json.JSONDecodeError, ValueError):
        payload = None
    if isinstance(payload, list):
        return [str(p).strip() for p in payload if str(p).strip()][:6]
    if isinstance(payload, dict) and isinstance(payload.get("candidates"), list):
        return [str(p).strip() for p in payload["candidates"] if str(p).strip()][:6]
    # Fallback: numbered/bulleted lines.
    lines = [
        re.sub(r"^[\d.\-*\s]+", "", ln).strip()
        for ln in _strip_code_fence(raw).splitlines()
        if ln.strip()
    ]
    return [ln for ln in lines if len(ln) > 20][:6]


async def default_proposer(prompt_key: str, baseline_prompt: str, count: int) -> list[str]:
    """LLM proposes ``count`` rewritten candidate prompts (never raises)."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior prompt engineer. Rewrite the given prompt to "
                "improve reliability for its task. Return STRICT JSON only: "
                '{"candidates": ["...", "..."]} — no prose, no code fences.'
            ),
        },
        {"role": "user", "content": f"Prompt key: {prompt_key}\n\nCurrent prompt:\n{baseline_prompt}"},
    ]
    try:
        raw = await ai_client.completion(
            messages=messages,
            model="complex_reasoning",
            temperature=0.6,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        candidates = _parse_candidates(raw)
        logger.info("optimizer_candidates_proposed", prompt_key=prompt_key, count=len(candidates))
        return candidates[: count]
    except Exception as exc:
        logger.warning("optimizer_propose_failed", error=str(exc), prompt_key=prompt_key)
        return []


def _case_score(response: str, case: Case) -> float:
    """Deterministic per-case rubric so the optimizer works fully offline."""
    text = (response or "").strip()
    if not text:
        return 0.0
    ideal = str(case.get("ideal") or "").lower()
    if "refuse" in ideal:
        return 1.0 if _REFUSAL_HINTS.search(text) else 0.5
    if "short" in ideal:
        return 1.0 if len(text.split()) <= 30 else 0.5
    return 1.0 if len(text.split()) >= 8 else 0.5


async def default_judge(prompt: str, cases: list[Case]) -> float:
    """Vanilla LLM judge: applies the candidate prompt to each golden case and
    scores the outputs with the deterministic rubric. Never raises."""
    scores: list[float] = []
    for case in cases:
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": str(case.get("input") or "")},
        ]
        try:
            out = await ai_client.completion(
                messages=messages,
                model="fast_chat",
                temperature=0.0,
                max_tokens=200,
            )
        except Exception:
            continue
        scores.append(_case_score(out, case))
    return mean(scores) if scores else 0.0


class PromptOptimizationService:
    """One closed optimization loop, persisted for the audit trail."""

    def __init__(
        self,
        proposer: Proposer = default_proposer,
        judge: Judge = default_judge,
        default_candidates: int | None = None,
    ) -> None:
        self._proposer = proposer
        self._judge = judge
        self._default_candidates = (
            default_candidates
            if default_candidates is not None
            else settings.OPTIMIZATION_DEFAULT_CANDIDATES
        )

    async def run(
        self,
        session,
        *,
        prompt_key: str,
        baseline_prompt: str,
        cases: list[Case],
        candidate_count: int | None = None,
        promote_only_if_better: bool = True,
        margin: float = 0.05,
    ) -> PromptOptimizationRun:
        """
        Propose → score → promote → persist. Never raises; an LLM outage just
        records a run that kept the baseline.
        """
        count = candidate_count or self._default_candidates
        run = PromptOptimizationRun(
            prompt_key=prompt_key,
            baseline_prompt=baseline_prompt,
            status="running",
            candidate_count=count,
        )
        repo = BaseRepository(session, PromptOptimizationRun)
        created = await repo.create(run)

        try:
            baseline_score = await self._judge(baseline_prompt, cases)

            candidates = await self._proposer(prompt_key, baseline_prompt, count)
            scored: list[dict[str, Any]] = []
            for i, candidate in enumerate(candidates):
                try:
                    score = await self._judge(candidate, cases)
                except Exception as exc:
                    logger.warning("optimizer_candidate_judge_failed", index=i, error=str(exc))
                    continue
                scored.append({"index": i, "score": round(score, 4), "prompt": candidate[:500]})

            best = max(scored, key=lambda s: s["score"], default=None)
            accepted_variant = baseline_prompt
            if best and not (promote_only_if_better and best["score"] <= baseline_score + margin):
                accepted_variant = best["prompt"]
                logger.info(
                    "optimizer_promoted_candidate",
                    prompt_key=prompt_key,
                    score=best["score"],
                    baseline=round(baseline_score, 4),
                )
            else:
                logger.info("optimizer_kept_baseline", prompt_key=prompt_key)

            created.status = "completed"
            created.accepted_variant = accepted_variant
            created.baseline_score = round(baseline_score, 4)
            created.best_score = round(best["score"], 4) if best else round(baseline_score, 4)
            created.average_score = (
                round(mean(s["score"] for s in scored), 4) if scored else round(baseline_score, 4)
            )
            created.details = {
                "candidate_count_actual": len(scored),
                "candidates": scored,
                "promoted": accepted_variant != baseline_prompt,
                "cases_evaluated": len(cases),
                "promote_only_if_better": promote_only_if_better,
                "margin": margin,
            }
        except Exception as exc:
            logger.warning("optimizer_run_failed", error=str(exc), prompt_key=prompt_key)
            created.status = "failed"
            created.details = {"error": str(exc)[:500]}

        await repo.update(created, {"status": created.status})
        return created


prompt_optimizer_service = PromptOptimizationService()
