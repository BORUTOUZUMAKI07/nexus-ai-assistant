"""
Deterministic Canary/Shadow Experiments (safe-release pattern).

User-bucket assignment for gradual, shadow-released prompt/model changes:

* ``bucketed_variant`` — HMAC-SHA256 keyed to a stable 0..N-1 bucket, so the
  same user always lands in the same bucket for the same experiment+key pair
  (no thrashing between variants across requests).
* ``variant_for`` — maps a bucket to a labeled variant using configurable
  weights (e.g. control 90% / canary 10%).
* The system defaults to a single ``default`` variant: experiments are a
  no-op until explicitly configured, so enabling this subsystem never changes
  production behavior by itself.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

import structlog
import yaml
from backend.app.core.config import settings

logger = structlog.get_logger(__name__)

_DEFAULT_EXPERIMENT_CONFIG: dict[str, Any] = {}


def _load_experiment_config() -> dict[str, Any]:
    path = settings.EXPERIMENTS_CONFIG_PATH
    if not path:
        return _DEFAULT_EXPERIMENT_CONFIG
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("experiments_config_load_failed", path=path, error=str(exc))
        return _DEFAULT_EXPERIMENT_CONFIG


_EXPERIMENT_CONFIG = _load_experiment_config()


def bucketed_variant(key: str, total_buckets: int | None = None) -> int:
    """Deterministic 0..N-1 bucket for any stable string key."""
    total = total_buckets or settings.EXPERIMENTS_DEFAULT_BUCKETS
    if total < 1:
        total = 1
    digest = hmac.new(b"nexus-experiment", key.encode("utf-8"), hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big") % total


def variant_for(
    key: str,
    variants: list[str],
    weights: list[int] | None = None,
    total_buckets: int | None = None,
) -> str:
    """
    Assign ``key`` to a variant honoring relative weights.

    ``variants`` and (optionally) ``weights`` must have equal length; weights
    default to equal split. Bucket boundaries are computed from cumulative
    weight ratios, so the mapping is stable regardless of request count.
    """
    if not variants:
        return "default"
    if weights is None:
        weights = [1] * len(variants)
    if len(variants) != len(weights):
        raise ValueError("variants and weights must have equal length")

    total = sum(max(0, w) for w in weights) or 1
    bucket = bucketed_variant(key, total_buckets)
    bucket_ratio = bucket / (total_buckets or settings.EXPERIMENTS_DEFAULT_BUCKETS)

    cumulative = 0.0
    for variant, weight in zip(variants, weights):
        cumulative += max(0, weight) / total
        if bucket_ratio < cumulative:
            return variant
    return variants[-1]


class ExperimentService:
    """
    Resolves experiment→variant assignments for a user. Reads experiment
    definitions from ``EXPERIMENTS_CONFIG_PATH`` (YAML)::

        experiments:
          chat_system_prompt:
            variants: ["control", "canary_balanced"]
            weights: [90, 10]
            default: "control"

    Unconfigured experiments return their declared default (or "default").
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config if config is not None else _EXPERIMENT_CONFIG

    def list_experiments(self) -> list[str]:
        return list((self._config.get("experiments") or {}).keys())

    def definition(self, experiment: str) -> dict[str, Any]:
        """Raw experiment definition (variants, weights, bandit mode, epsilon)."""
        experiments = self._config.get("experiments") or {}
        return experiments.get(experiment) or {}

    def variant_for_user(self, user_id: str, experiment: str) -> str:
        experiments = self._config.get("experiments") or {}
        definition = experiments.get(experiment)
        if not definition:
            return "default"
        variants = definition.get("variants", [])
        weights = definition.get("weights")
        fallback = definition.get("default", variants[0] if variants else "default")
        if not variants:
            return fallback
        try:
            return variant_for(
                key=f"{experiment}:{user_id}",
                variants=variants,
                weights=weights,
            )
        except Exception as exc:
            logger.warning("experiment_variant_resolution_failed", experiment=experiment, error=str(exc))
            return fallback


experiment_service = ExperimentService()
