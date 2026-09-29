"""
Bandit Exploration (ε-greedy core, IPS-style reward stream).

Industry default for serve-time optimization: keep exploring a little
(ε-greedy) while exploiting the empirically-best variant, learning from real
user feedback instead of static HMAC weights. To stay faithful to the approved
stack there is no third-party bandit library — the math is ~40 lines and fully
deterministic/testable:

* ``greedy_choice``   — pure ε-greedy arm selection over empirical means.
* ``select``          — async arm selection using recent rewards from
                        ``bandit_rewards`` (no-op unless the experiment config
                        declares ``bandit: true``, preserving existing behavior).
* ``record_reward``   — appended on every thumbs-up/thumbs-down so win rates
                        converge on real outcome data.
* ``stats``           — aggregate view for the admin surface.
"""
from __future__ import annotations

import random
from typing import Any
from uuid import UUID

import structlog
from backend.app.core.config import settings
from backend.app.domain.base_repository import BaseRepository
from backend.app.domain.experiment.models import BanditReward
from backend.app.services.experiments import experiment_service
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class BanditService:
    """ε-greedy multi-armed bandit over experiment variants (reward-driven)."""

    def __init__(
        self,
        epsilon: float | None = None,
        window: int | None = None,
        rng: Any = random,
    ) -> None:
        self._epsilon = settings.BANDIT_EPSILON if epsilon is None else epsilon
        self._window = window if window is not None else settings.BANDIT_REWARD_WINDOW
        self._rng = rng

    # ── pure selection math ───────────────────────────────────────────────────
    def greedy_choice(self, means: dict[str, float], epsilon: float | None = None) -> str:
        """ε-greedy from empirical means; explores uniformly with probability ε."""
        variants = list(means.keys())
        if not variants:
            return ""
        eps = self._epsilon if epsilon is None else epsilon
        if self._rng.random() < eps:
            return self._rng.choice(variants)
        return max(variants, key=lambda v: means.get(v, 0.0))

    # ── async integration ─────────────────────────────────────────────────────
    async def _empirical_means(
        self, session: AsyncSession, experiment_key: str, variants: list[str]
    ) -> dict[str, float]:
        stmt = select(BanditReward).where(BanditReward.experiment_key == experiment_key)
        rows = (await session.exec(stmt)).all()
        sums: dict[str, float] = {}
        counts: dict[str, int] = {}
        for row in rows:
            if row.variant not in variants:
                continue
            sums[row.variant] = sums.get(row.variant, 0.0) + float(row.reward or 0.0)
            counts[row.variant] = counts.get(row.variant, 0) + 1
        return {
            variant: sums[variant] / counts[variant]
            for variant in variants
            if counts.get(variant)
        }

    async def select(self, session: AsyncSession, experiment: str, user_key: str = "") -> str:
        """
        ε-greedy selection for an experiment. Returns ``""`` (no-op) unless the
        experiment config declares ``bandit: true``, so default deployments keep
        their current HMAC-weighted assignment exactly as before.
        """
        definition = experiment_service.definition(experiment)
        if not definition.get("bandit"):
            return ""
        variants = definition.get("variants") or []
        if not variants:
            return ""
        means = await self._empirical_means(session, experiment, variants)
        if not means:
            # Cold start: uniform exploration across arms.
            return self._rng.choice(variants)
        choice = self.greedy_choice(means)
        logger.info(
            "bandit_select", experiment=experiment, choice=choice,
            means={v: round(m, 4) for v, m in means.items()},
        )
        return choice

    async def record_reward(
        self,
        session: AsyncSession,
        experiment: str,
        variant: str,
        reward: float,
        user_id: UUID | None = None,
    ) -> BanditReward | None:
        """Persist one reward observation (thumbs_up=1.0 / thumbs_down=0.0)."""
        if not variant or variant == "default":
            return None
        record = BanditReward(
            experiment_key=experiment,
            variant=variant,
            reward=float(reward),
            user_id=user_id,
        )
        try:
            repo = BaseRepository(session, BanditReward)
            return await repo.create(record)
        except Exception as exc:
            logger.warning("bandit_reward_record_failed", error=str(exc), experiment=experiment)
            return None

    async def stats(
        self, session: AsyncSession, experiment: str | None = None
    ) -> list[dict[str, Any]]:
        """Empirical win rates per (experiment, variant) over the reward window."""
        stmt = select(BanditReward)
        if experiment:
            stmt = stmt.where(BanditReward.experiment_key == experiment)
        rows = (await session.exec(stmt)).all()
        counts: dict[tuple[str, str], list[float]] = {}
        for row in rows:
            counts.setdefault((row.experiment_key, row.variant), []).append(float(row.reward or 0.0))
        stats: list[dict[str, Any]] = []
        for (exp_key, variant), rewards in sorted(counts.items()):
            recent = rewards[-self._window :]
            stats.append(
                {
                    "experiment": exp_key,
                    "variant": variant,
                    "reward_count": len(rewards),
                    "window_reward_count": len(recent),
                    "mean_reward": round(sum(recent) / len(recent), 4),
                }
            )
        return stats


bandit_service = BanditService()
