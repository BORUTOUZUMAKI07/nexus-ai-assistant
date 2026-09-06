"""
Retrieval Critique Service (Critic/Grader).
Grades the quality of retrieved local RAG context before synthesis and decides
whether Conditional Retrieval-Augmented Generation (CRAG) web search is needed:

  - relevant      → local RAG context is sufficient; synthesize directly
  - insufficient  → some signal but weak; retrieve web context to reinforce
  - unrelated     → no useful grounding; fall back to web search

Kept deterministic (score-driven) so the verdict is testable; an optional LLM
layer can be layered on later without changing the service contract.
"""
from statistics import mean
from typing import Any

import structlog
from backend.app.domain.file.schemas import RAGCitation

logger = structlog.get_logger(__name__)

VERDICT_RELEVANT = "relevant"
VERDICT_INSUFFICIENT = "insufficient"
VERDICT_UNRELATED = "unrelated"


class RetrievalCritiqueService:
    """Abstraction for grading local RAG retrieval quality."""

    def grade(
        self,
        citations: list[RAGCitation],
        relevant_threshold: float = 0.30,
        insufficient_floor: float = 0.15,
    ) -> tuple[str, float]:
        """
        Returns (verdict, relevance_score).

        - No citations             → ("unrelated", 0.0)
        - avg score ≥ threshold    → ("relevant", avg)
        - avg score ≥ floor        → ("insufficient", avg)
        - otherwise                → ("unrelated", avg)
        """
        citations = citations or []
        if not citations:
            logger.info("critique_graded", verdict=VERDICT_UNRELATED, score=0.0, reason="no_citations")
            return VERDICT_UNRELATED, 0.0

        score = mean(float(c.score) for c in citations)
        if score >= relevant_threshold:
            verdict = VERDICT_RELEVANT
        elif score >= insufficient_floor:
            verdict = VERDICT_INSUFFICIENT
        else:
            verdict = VERDICT_UNRELATED

        logger.info("critique_graded", verdict=verdict, score=round(score, 4), citation_count=len(citations))
        return verdict, score

    def summarize(
        self,
        verdict: str,
        score: float,
        citation_count: int,
    ) -> dict[str, Any]:
        """Builds the state payload written by the critic node."""
        return {
            "grader_verdict": verdict,
            "rag_relevance_score": round(score, 4),
            "grader_confidence": round(max(0.0, min(1.0, score)), 4),
            "citation_count": citation_count,
        }


retrieval_critique_service = RetrievalCritiqueService()
