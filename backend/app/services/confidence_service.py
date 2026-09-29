"""
Calibrated Confidence Gate: a validated post-hoc confidence calibration.

Composite confidence for a generated response: groundedness against retrieved
citations (the dominant signal for RAG answers), retrieval coverage, and an
output-quantity term. A calibrated threshold (derived from a reward/outcome
sample set when one is available, else a configured default) turns the score
into a decision: answer vs hedge. Deterministic and LLM-free so it is cheap,
testable, and never blocks a response.

The gate is deliberately a *decision recorder* at the orchestrator level
(fail-open): a low-confidence verdict never crashes or rewrites a response, it
is recorded on the state so any upstream consumer (HITL, admin dashboards) can
act on it.
"""
from __future__ import annotations

from statistics import mean
from typing import Any

import structlog
from backend.app.core.config import settings

logger = structlog.get_logger(__name__)

_GROUNDING_WEIGHT = 0.55
_COVERAGE_WEIGHT = 0.35
_VERBOSITY_WEIGHT = 0.10
_UNGROUNDED_CHAT_CONFIDENCE = 0.95
_MIN_CALIBRATION_SAMPLES = 10


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


class ConfidenceService:
    """Composite confidence scoring + calibrated decision boundary."""

    def __init__(self, threshold: float | None = None) -> None:
        self._default_threshold = (
            threshold if threshold is not None else settings.CONFIDENCE_THRESHOLD
        )

    def score(
        self,
        response_text: str,
        citations: list[Any] | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        """
        Deterministic confidence components for a response.

        * ``grounded``      — whether any citations were provided
        * ``grounding``     — fraction of the response attributable to the
                              retrieved evidence (0..1; 0 when ungrounded)
        * ``coverage``      — mean retrieval score of the citations (0..1)
        * ``verbosity``     — responses of 0 or >~400 words get penalized
                              (empty answers and walls of text are both weak)
        * ``confidence``    — weighted composite (0..1)
        """
        citations = citations or []
        if not citations:
            return {
                "grounded": False,
                "grounding": 0.0,
                "coverage": 0.0,
                "verbosity": _verbosity_score(response_text),
                "confidence": _UNGROUNDED_CHAT_CONFIDENCE,
            }

        normalized = _citation_models(citations)
        grounding = _grounding_score(response_text, normalized["contents"])
        coverage = _clamp(mean(normalized["scores"]) if normalized["scores"] else 0.0)
        verbosity = _verbosity_score(response_text)
        confidence = _clamp(
            _GROUNDING_WEIGHT * grounding
            + _COVERAGE_WEIGHT * coverage
            + _VERBOSITY_WEIGHT * verbosity
        )
        return {
            "grounded": True,
            "grounding": round(grounding, 4),
            "coverage": round(coverage, 4),
            "verbosity": round(verbosity, 4),
            "confidence": round(confidence, 4),
        }

    def calibrated_threshold(
        self, samples: list[dict[str, Any]] | None = None
    ) -> tuple[float, bool]:
        """
        Returns ``(threshold, calibrated)``.

        Calibration from a reward sample set: split confident outputs by their
        outcome (1 = good / 0 = bad), place the boundary between the two group
        means. Falls back to the configured default when the sample set is too
        small — the honest default rather than a spurious "calibrated" number.
        """
        samples = samples or []
        if len(samples) < _MIN_CALIBRATION_SAMPLES:
            return self._default_threshold, False

        positives = [s["confidence"] for s in samples if s.get("outcome", 0) >= 0.5]
        negatives = [s["confidence"] for s in samples if s.get("outcome", 0) < 0.5]
        if not positives or not negatives:
            return self._default_threshold, False
        boundary = (mean(positives) + mean(negatives)) / 2.0
        return _clamp(boundary), True

    def decide(
        self,
        response_text: str,
        citations: list[Any] | None = None,
        samples: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Full decision: confidence components + threshold + verdict."""
        components = self.score(response_text, citations)
        threshold, calibrated = self.calibrated_threshold(samples)
        passed = components["confidence"] >= threshold
        decision = {
            "calibrated": calibrated,
            "threshold": round(threshold, 4),
            "confidence": components["confidence"],
            "pass": passed,
            "action": "answer" if passed else "hedge",
            "grounded": components["grounded"],
            "components": {k: v for k, v in components.items() if k not in ("confidence", "grounded")},
        }
        logger.info(
            "confidence_gate_decided",
            confidence=components["confidence"],
            threshold=round(threshold, 4),
            action=decision["action"],
            calibrated=calibrated,
        )
        return decision


def _grounding_score(response_text: str, contents: list[str]) -> float:
    """Share of response words that overlap with the retrieved evidence set."""
    response_words = set(_tokens(response_text))
    if not response_words:
        return 0.0
    evidence_words: set[str] = set()
    for content in contents:
        evidence_words |= set(_tokens(content))
    overlap = len(response_words & evidence_words) / len(response_words)
    return _clamp(overlap)


def _verbosity_score(response_text: str) -> float:
    """Penalize empty / extremely short and wall-of-text responses."""
    words = len(_tokens(response_text))
    if words == 0:
        return 0.0
    if words < 8:
        return 0.3
    if words > 400:
        return 0.6
    return 1.0


def _tokens(text: str) -> list[str]:
    return [t.lower().strip(".,;:!?()[]{}\"'") for t in str(text or "").split() if t.strip()]


def _citation_attr(citation: Any, name: str, fallback: Any = None) -> Any:
    """Read an attribute off a RAGCitation object or a plain dict citation."""
    if isinstance(citation, dict):
        return citation.get(name, fallback)
    return getattr(citation, name, fallback)


def _citation_content(citation: Any) -> str:
    for name in ("content_snippet", "content", "snippet"):
        value = _citation_attr(citation, name)
        if value:
            return str(value)
    return ""


def _citation_score(citation: Any) -> float:
    try:
        value = _citation_attr(citation, "score", 0.0) or 0.0
        return _clamp(float(value))
    except (TypeError, ValueError):
        return 0.0


def _citation_models(citations: list[Any]) -> dict[str, Any]:
    """Normalize heterogeneous citation inputs (objects or dicts)."""
    scores = [_citation_score(c) for c in citations]
    contents = [_citation_content(c) for c in citations]
    return {"scores": scores, "contents": contents}


confidence_service = ConfidenceService()
