"""
RAG Reranking Service.
Re-orders retrieved candidate chunks using fast cross-encoder models
(FlashRank or lightweight cross-attention scoring).
"""
from typing import Any

import structlog
from backend.app.services.rag.base import IReranker

logger = structlog.get_logger(__name__)


class RerankingService(IReranker):
    """
    Reranker to prioritize high-precision chunks and filter out low-relevance noise.
    """

    def __init__(self, model_name: str = "ms-marco-TinyBERT-L-2-v2"):
        self.model_name = model_name

    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_n: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Rerank retrieved chunks by relevance to the query.
        """
        if not candidates:
            return []

        try:
            from flashrank import Ranker, RerankRequest

            ranker = Ranker(model_name=self.model_name)
            passages = [
                {"id": str(i), "text": c.get("content", ""), "meta": c}
                for i, c in enumerate(candidates)
            ]
            rerank_request = RerankRequest(query=query, passages=passages)
            results = ranker.rerank(rerank_request)

            reranked: list[dict[str, Any]] = []
            for r in results[:top_n]:
                chunk_data = r["meta"]
                chunk_data["rerank_score"] = float(r.get("score", 0.0))
                reranked.append(chunk_data)

            logger.info("flashrank_rerank_complete", original_count=len(candidates), reranked_count=len(reranked))
            return reranked

        except Exception as exc:
            logger.warning("flashrank_unavailable_using_vector_score_order", error=str(exc))
            # Fallback: Sort by original vector fusion score
            sorted_candidates = sorted(
                candidates,
                key=lambda x: x.get("score", 0.0),
                reverse=True,
            )
            return sorted_candidates[:top_n]


# Singleton instance for backwards-compatibility
reranker = RerankingService()


def get_reranker() -> IReranker:
    """Dependency provider returning the active IReranker implementation."""
    return reranker
