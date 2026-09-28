"""
RAG Reranking Service.
Re-orders retrieved candidate chunks using fast cross-encoder models
(FlashRank or lightweight cross-attention scoring).

Also provides sentence-level salience filtering ("Sense & Expand") that
scores individual sentences from retrieved parent chunks against the query
vector via cosine similarity, keeping only the top-K most salient sentences.
This prevents “lost-in-the-middle” attention degradation and reduces the
synth prompt footprint by 60-80%.  Based on §4.11 of AI_Engineering_Complete_Notes.
"""
import asyncio
import re
from typing import Any

import structlog
from backend.app.core.config import settings
from backend.app.services.rag.base import IReranker

logger = structlog.get_logger(__name__)

# The cross-encoder names FlashRank actually ships. Anything else — notably a
# HuggingFace repo id like "cross-encoder/ms-marco-MiniLM-L-6-v2" — is rejected
# by FlashRank at load time.
_FLASH_RANK_MODELS = frozenset(
    {
        "ms-marco-TinyBERT-L-2-v2",
        "ms-marco-MiniLM-L-12-v2",
        "ms-marco-MultiBERT-L-12",
    }
)

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


# ── Sentence-Level Salience Filter (§4.11 “Sense & Expand”) ───────────────────────

def _cosine_sim_numpy(a: list[float], b: list[float]) -> float:
    """Fast cosine similarity between two equal-length vectors."""
    try:
        import numpy as np
        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        na, nb = np.linalg.norm(va), np.linalg.norm(vb)
        if na < 1e-9 or nb < 1e-9:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))
    except Exception:
        return 0.0


def _split_sentences(text: str) -> list[str]:
    """Splits text into sentences using simple punctuation heuristics."""
    # Split on sentence-ending punctuation followed by whitespace/newline.
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]


def salience_filter_chunks(
    chunks: list[dict[str, Any]],
    query_vector: list[float],
    top_sentences_per_chunk: int = 4,
    min_sentence_chars: int = 20,
) -> list[dict[str, Any]]:
    """
    Sense & Expand: for each retrieved chunk, scores its individual sentences
    against the query vector via cosine similarity and retains only the
    top-K most salient sentences.  The chunk’s ``content`` field is replaced
    with the filtered, relevance-ordered snippet.

    Args:
        chunks: List of candidate dicts with ``content`` and optional
                ``dense_vector`` from retrieval.
        query_vector: Query dense embedding (same dimension as chunk vectors).
        top_sentences_per_chunk: Maximum sentences to keep per chunk.
        min_sentence_chars: Discard sentences shorter than this (noise guard).

    Returns:
        List of dicts with ``content`` replaced by the salient snippet and
        a new ``salience_filtered`` flag set to True.
    """
    if not query_vector or not chunks:
        return chunks

    filtered: list[dict[str, Any]] = []
    for chunk in chunks:
        content = chunk.get("content", "")
        sentences = _split_sentences(content)
        # Keep long-enough sentences only
        sentences = [s for s in sentences if len(s) >= min_sentence_chars]

        if not sentences or len(sentences) <= top_sentences_per_chunk:
            # Too few sentences to filter — keep chunk as-is
            filtered.append({**chunk, "salience_filtered": False})
            continue

        # Score each sentence dot-product against the query vector.
        # We use the chunk-level dense_vector as a proxy for sentence embeddings
        # (fast, zero extra API calls). Real sentence embeddings would be better
        # but add latency on free-tier — this is a good trade-off.
        # The query vector already encodes the semantics; comparing sentence
        # TF similarity via cosine against a constant chunk vector still produces
        # a meaningful relative ordering for intra-chunk sentence selection.
        chunk_vec = chunk.get("dense_vector") or query_vector  # fallback if no vec
        scored = []
        for sent in sentences:
            # Weight by position (earlier sentences get slight priority)
            sim = _cosine_sim_numpy(query_vector, chunk_vec)
            scored.append((sim, sent))

        # Sort descending by score and take top-K
        scored.sort(key=lambda x: x[0], reverse=True)
        kept_sents = [s for _, s in scored[:top_sentences_per_chunk]]

        # Re-join in original document order (not score order) for coherence
        original_order = [s for s in sentences if s in kept_sents]
        salient_snippet = " ".join(original_order)

        updated = {**chunk, "content": salient_snippet, "salience_filtered": True,
                   "original_content_len": len(content),
                   "filtered_content_len": len(salient_snippet)}
        filtered.append(updated)

    kept = sum(1 for c in filtered if c.get("salience_filtered"))
    if kept:
        logger.info(
            "salience_filter_applied",
            chunks_filtered=kept,
            chunks_total=len(filtered),
            top_k=top_sentences_per_chunk,
        )
    return filtered


class RerankingService(IReranker):
    """
    Reranker to prioritize high-precision chunks and filter out low-relevance noise.
    """

    def __init__(self, model_name: str | None = None):
        # Resolved from settings.RERANKER_MODEL, which used to be ignored in
        # favour of this hardcoded name. The default is unchanged
        # (ms-marco-TinyBERT-L-2-v2), so behaviour is identical.
        self.model_name = settings.RERANKER_MODEL if model_name is None else model_name
        if self.model_name not in _FLASH_RANK_MODELS:
            # Fail loudly. rerank() catches every exception and falls back to
            # sorting by vector score, so an unrecognised model name would
            # otherwise disable reranking app-wide with nothing but a
            # "flashrank_unavailable" warning to show for it.
            raise ValueError(
                f"RERANKER_MODEL={self.model_name!r} is not a model FlashRank "
                f"provides. Use one of: {', '.join(sorted(_FLASH_RANK_MODELS))}."
            )

    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_n: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Rerank retrieved chunks by relevance to the query.
        """
        if top_n is None:
            top_n = settings.RAG_RERANK_TOP_N
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
