"""
RAG Reranking Service.
Re-orders retrieved candidate chunks using fast cross-encoder models
(FlashRank or lightweight cross-attention scoring).
"""
import asyncio
from typing import Any

import structlog
from backend.app.services.rag.base import IReranker

logger = structlog.get_logger(__name__)

# Ranker construction loads model weights — cache one instance per model name
# and reuse it, instead of re-initializing the model for every rerank call.
_ranker_cache: dict[str, Any] = {}


def _get_ranker(model_name: str) -> Any:
    from flashrank import Ranker

    cached = _ranker_cache.get(model_name)
    if cached is None:
        cached = Ranker(model_name=model_name)
        _ranker_cache[model_name] = cached
    return cached


def _rerank_sync(ranker: Any, request: Any, top_n: int, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = ranker.rerank(request)
    reranked: list[dict[str, Any]] = []
    for r in results[:top_n]:
        chunk_data = r["meta"]
        chunk_data["rerank_score"] = float(r.get("score", 0.0))
        reranked.append(chunk_data)
    return reranked


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
            from flashrank import RerankRequest

            # Ranker construction + CPU-bound inference must never block the
            # event loop.  Init is cached, so first-call overhead amortises.
            ranker = await asyncio.to_thread(_get_ranker, self.model_name)
            passages = [
                {"id": str(i), "text": c.get("content", ""), "meta": c}
                for i, c in enumerate(candidates)
            ]
            rerank_request = RerankRequest(query=query, passages=passages)
            reranked = await asyncio.to_thread(_rerank_sync, ranker, rerank_request, top_n, candidates)

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
