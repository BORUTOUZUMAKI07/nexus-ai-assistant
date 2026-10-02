"""
CRAG-style Corrective Retrieval (replaces the REFRAG candidate, arXiv:2509.01092 is
decoder-side and out-of-scope on managed providers; arXiv:2401.15884 CRAG is the
production standard: a retriever-grader + re-retrieve / refine / fallback loop).

``RetrievalGuardService.correct_retrieval`` closes the loop on a weak retrieval
grade: instead of jumping straight to web search, it re-queries local RAG once
with a refined query variant ("re-retrieve"), re-grades the result, and only if
still insufficient/unrelated leaves the web-fallback decision to the caller.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from backend.app.core.config import settings
from backend.app.domain.file.schemas import RAGCitation, RAGQueryResult
from backend.app.services.rag.critique import (
    RetrievalCritiqueService,
    retrieval_critique_service,
)
from backend.app.services.rag.query_rewriter import (
    QueryRewriterService,
    query_rewriter_service,
)

logger = structlog.get_logger(__name__)


class RetrievalGuardService:
    """Retriever-grader with corrective re-retrieval (CRAG, bounded revisions)."""

    def __init__(
        self,
        critique: RetrievalCritiqueService = retrieval_critique_service,
        rewriter: QueryRewriterService = query_rewriter_service,
        max_revisions: int | None = None,
    ) -> None:
        self._critique = critique
        self._rewriter = rewriter
        self._max_revisions = max_revisions if max_revisions is not None else settings.CRAG_MAX_REVISIONS

    async def correct_retrieval(
        self,
        *,
        query: str,
        user_id: UUID,
        citations: list[RAGCitation],
        verdict: str,
        rag,  # RAGService dependency (or any object exposing async query(...))
        file_ids: list[UUID] | None = None,
    ) -> dict[str, Any]:
        """
        Attempt a bounded corrective re-query when the initial grade is weak.

        Returns a payload with:
          * ``action``            — ``"none"`` | ``"re-retrieved"``
          * ``citations``         — best citations after correction
          * ``verdict``           — re-graded verdict
          * ``score``             — re-graded relevance score
          * ``refined_queries``   — the query variants used for re-retrieval
          * ``revision_count``    — how many corrective passes ran
        """
        if verdict == "relevant" or not citations:
            return {
                "action": "none",
                "citations": citations,
                "verdict": verdict,
                "score": 0.0,
                "refined_queries": [],
                "revision_count": 0,
            }

        best_citations: list[RAGCitation] = list(citations)
        best_verdict = verdict
        best_score = max(0.0, float(getattr(citations[0], "score", 0.0) or 0.0)) if citations else 0.0
        refined_queries: list[str] = []
        revision_count = 0
        changed = False

        for _ in range(max(0, self._max_revisions)):
            if best_verdict == "relevant":
                break
            # Refine the query: prefer a rewritten variant over the raw query so
            # the corrective pass searches different phrasing, not the same one.
            variants = await self._rewriter.rewrite(query)
            if not variants:
                break
            refined = next((v for v in variants if v.strip().lower() != query.strip().lower()), variants[-1])
            refined_queries.append(refined)
            try:
                result: RAGQueryResult = await rag.query(
                    query=refined,
                    user_id=user_id,
                    file_ids=file_ids,
                    top_k=5,
                    score_threshold=0.35,
                )
            except Exception as exc:
                logger.warning("crag_corrective_requery_failed", error=str(exc), query=query)
                break
            new_citations = list(result.citations)
            if not new_citations:
                break
            new_verdict, new_score = self._critique.grade(new_citations)
            revision_count += 1
            # Accept the correction only when it is strictly better (avoids
            # thrashing between equally-weak candidate sets).
            if new_score > best_score:
                best_citations = new_citations
                best_verdict = new_verdict
                best_score = new_score
                changed = True
                if new_verdict == "relevant":
                    break
            else:
                break

        action = "re-retrieved" if changed else "none"
        logger.info(
            "crag_corrective_retrieval",
            action=action,
            verdict=best_verdict,
            score=round(best_score, 4),
            revision_count=revision_count,
        )
        return {
            "action": action,
            "citations": best_citations,
            "verdict": best_verdict,
            "score": best_score,
            "refined_queries": refined_queries,
            "revision_count": revision_count,
        }


retrieval_guard_service = RetrievalGuardService()
