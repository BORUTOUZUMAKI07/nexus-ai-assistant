"""RAG Retrieval Service.
Coordinates dense & sparse embedding generation and dispatches native
Hybrid Search with Reciprocal Rank Fusion (RRF) to Qdrant.
"""
import asyncio
from typing import Any
from uuid import UUID

import structlog
from backend.app.core.config import settings
from backend.app.infrastructure.vector.base import IVectorStore
from backend.app.infrastructure.vector.qdrant_client import (
    vector_db as _default_vector_db,
)
from backend.app.services.rag.base import IRetriever

logger = structlog.get_logger(__name__)

# TextEmbedding downloads the ONNX model on first construction and would
# otherwise re-load it on every call. Cache one instance per model name.
_fastembed_model_cache: dict[str, Any] = {}


def _get_fastembed_model(model_name: str) -> Any:
    from fastembed import TextEmbedding

    cached = _fastembed_model_cache.get(model_name)
    if cached is None:
        cached = TextEmbedding(model_name=model_name)
        _fastembed_model_cache[model_name] = cached
    return cached


def _embed_texts_sync(model: Any, texts: list[str]) -> list[list[float]]:
    """Runs fastembed inference in a worker thread (blocking CPU/ONNX)."""
    embeddings = list(model.embed(texts))
    return [e.tolist() for e in embeddings]


async def _embed_with_gemini(
    texts: list[str],
    model: str,
    dimension: int,
    api_key: str,
) -> list[list[float]]:
    """
    Calls Google Generative Language API embedContent or batchEmbedContents.
    Uses Matryoshka outputDimensionality to match settings.EMBEDDING_DIMENSION.
    Batches in chunks of up to 100 texts (Gemini API limit).
    """
    import httpx

    clean_model = model if model.startswith("models/") else f"models/{model}"
    all_embeddings: list[list[float]] = []

    async with httpx.AsyncClient(timeout=45.0) as client:
        for i in range(0, len(texts), 100):
            batch = texts[i : i + 100]
            if len(batch) == 1:
                url = f"https://generativelanguage.googleapis.com/v1beta/{clean_model}:embedContent?key={api_key}"
                payload = {
                    "model": clean_model,
                    "content": {"parts": [{"text": batch[0]}]},
                    "outputDimensionality": dimension,
                }
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                vals = data.get("embedding", {}).get("values", [])
                if len(vals) != dimension:
                    raise RuntimeError(
                        f"Gemini returned embedding dimension {len(vals)} != expected {dimension}"
                    )
                all_embeddings.append(vals)
            else:
                url = f"https://generativelanguage.googleapis.com/v1beta/{clean_model}:batchEmbedContents?key={api_key}"
                payload = {
                    "requests": [
                        {
                            "model": clean_model,
                            "content": {"parts": [{"text": t}]},
                            "outputDimensionality": dimension,
                        }
                        for t in batch
                    ]
                }
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                embeddings = data.get("embeddings", [])
                for item in embeddings:
                    vals = item.get("values", [])
                    if len(vals) != dimension:
                        raise RuntimeError(
                            f"Gemini returned embedding dimension {len(vals)} != expected {dimension}"
                        )
                    all_embeddings.append(vals)

    return all_embeddings


def maximal_marginal_relevance(
    query_vector: list[float],
    candidate_vectors: list[list[float]],
    candidates: list[dict[str, Any]],
    top_k: int = 5,
    lambda_mult: float = 0.7,
) -> list[dict[str, Any]]:
    """
    Computes Maximal Marginal Relevance (MMR) over dense embeddings.
    Balances query relevance against novelty with already selected chunks:
      arg max [ lambda * Sim(query, d) - (1 - lambda) * max_{s in S} Sim(d, s) ]
    """
    import numpy as np

    if not candidates:
        return []

    # If any candidate vector is missing or malformed, gracefully fallback to candidate list
    if (
        not candidate_vectors
        or len(candidate_vectors) != len(candidates)
        or any(not v for v in candidate_vectors)
    ):
        return candidates[:top_k]

    q_vec = np.array(query_vector, dtype=np.float32)
    q_norm = np.linalg.norm(q_vec)
    if q_norm > 1e-9:
        q_vec = q_vec / q_norm

    doc_vecs = np.array(candidate_vectors, dtype=np.float32)
    doc_norms = np.linalg.norm(doc_vecs, axis=1, keepdims=True)
    doc_norms[doc_norms < 1e-9] = 1e-9
    doc_vecs = doc_vecs / doc_norms

    # Cosine similarities to the query
    query_sims = np.dot(doc_vecs, q_vec)

    selected_indices: list[int] = []
    unselected_indices = list(range(len(candidates)))
    target_k = min(top_k, len(candidates))

    # Pick the highest similarity candidate first
    best_first = int(np.argmax(query_sims))
    selected_indices.append(best_first)
    unselected_indices.remove(best_first)

    # Greedily pick remaining items using MMR formula
    while len(selected_indices) < target_k and unselected_indices:
        sub_vecs = doc_vecs[unselected_indices]
        selected_matrix = doc_vecs[selected_indices]

        # Inter-document cosine similarities: shape (len(unselected), len(selected))
        sims_to_selected = np.dot(sub_vecs, selected_matrix.T)
        max_sims_to_selected = np.max(sims_to_selected, axis=1)

        unselected_query_sims = query_sims[unselected_indices]

        mmr_scores = (
            lambda_mult * unselected_query_sims
            - (1.0 - lambda_mult) * max_sims_to_selected
        )

        best_idx_in_unselected = int(np.argmax(mmr_scores))
        chosen_cand_idx = unselected_indices[best_idx_in_unselected]

        selected_indices.append(chosen_cand_idx)
        unselected_indices.remove(chosen_cand_idx)

    selected_results: list[dict[str, Any]] = []
    for idx in selected_indices:
        item = dict(candidates[idx])
        item["mmr_selected"] = True
        selected_results.append(item)

    return selected_results


class RetrievalService(IRetriever):
    """
    Retrieves most relevant document chunks for a query using Hybrid RRF Search
    with optional Dense Maximal Marginal Relevance (MMR) diversification.
    """

    def __init__(
        self,
        embedding_model: str = settings.EMBEDDING_MODEL,
        vector_store: IVectorStore = _default_vector_db,
    ) -> None:
        self.embedding_model = embedding_model
        self._vector_store = vector_store

    async def generate_embedding(self, text: str) -> list[float]:
        """
        Generates dense vector embedding for a single text.
        """
        vectors = await self.generate_embeddings_batch([text])
        if not vectors:
            raise RuntimeError("Embedding generation returned empty response")
        return vectors[0]

    async def generate_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Generates dense vector embeddings for texts in batch.
        Uses Gemini API (0 MB RAM on Render) when GEMINI_API_KEY is configured,
        or falls back to local fastembed.
        """
        if not texts:
            return []

        try:
            # 1. Primary: Google Gemini API (0 MB local RAM, fast)
            if settings.GEMINI_API_KEY and (
                "gemini" in self.embedding_model.lower()
                or "text-embedding" in self.embedding_model.lower()
            ):
                return await _embed_with_gemini(
                    texts=texts,
                    model=self.embedding_model,
                    dimension=settings.EMBEDDING_DIMENSION,
                    api_key=settings.GEMINI_API_KEY,
                )

            # 2. Fallback: local FastEmbed CPU model
            model = await asyncio.to_thread(_get_fastembed_model, self.embedding_model)
            vectors = await asyncio.to_thread(_embed_texts_sync, model, texts)
            for vec in vectors:
                if len(vec) != settings.EMBEDDING_DIMENSION:
                    raise RuntimeError(
                        f"embedding dimension {len(vec)} != settings.EMBEDDING_DIMENSION "
                        f"({settings.EMBEDDING_DIMENSION}) — collection/ingest mismatch"
                    )
            return vectors
        except Exception as exc:
            logger.error(
                "embedding_generation_failed",
                error=str(exc),
                model=self.embedding_model,
                count=len(texts),
            )
            raise

    def generate_sparse_vector(self, text: str) -> dict[str, Any]:
        """
        Generates sparse BM25 token frequencies.

        Uses a stable per-token hash (hashlib md5, not builtin `hash`, whose
        value is randomized per-process) so indices match between the indexing
        worker and the querying process.
        """
        import hashlib
        import re
        from collections import Counter

        words = re.findall(r"\w+", text.lower())
        counts = Counter(words)
        # Stable index across processes (derive from md5 digest, not hash()).
        # Merge colliding hashes by summing values so indices never repeat —
        # Qdrant 422s on duplicate sparse indices.
        merged: dict[int, float] = {}
        for w, c in counts.items():
            idx = int(hashlib.md5(w.encode("utf-8")).hexdigest()[:8], 16) % 100000
            merged[idx] = merged.get(idx, 0.0) + float(c)
        return {
            "indices": list(merged.keys()),
            "values": list(merged.values()),
        }

    async def retrieve(
        self,
        query: str,
        user_id: UUID,
        file_ids: list[UUID] | None = None,
        top_k: int = 5,
        score_threshold: float = 0.35,
        with_vectors: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Hybrid retrieval pipeline.
        """
        logger.info("retrieving_chunks_for_query", query=query, user_id=str(user_id), top_k=top_k)

        dense_vector = await self.generate_embedding(query)
        sparse_vec = self.generate_sparse_vector(query)

        filter_conditions: dict[str, Any] = {"user_id": str(user_id)}
        if file_ids:
            filter_conditions["file_id"] = [str(fid) for fid in file_ids]

        results = await self._vector_store.hybrid_search_with_fusion(
            dense_vector=dense_vector,
            sparse_indices=sparse_vec["indices"],
            sparse_values=sparse_vec["values"],
            limit=top_k,
            filter_conditions=filter_conditions,
            score_threshold=score_threshold,
            with_vectors=with_vectors,
        )

        return results

    async def retrieve_multi(
        self,
        queries: list[str],
        user_id: UUID,
        file_ids: list[UUID] | None = None,
        top_k: int = 5,
        score_threshold: float = 0.35,
        use_mmr: bool = True,
        mmr_lambda: float = 0.7,
    ) -> list[dict[str, Any]]:
        """
        Multi-query retrieval with optional Dense MMR: runs hybrid search for every variant,
        fetches dense vectors, resolves child hits back to their parent chunks (deduplicated),
        and applies Maximal Marginal Relevance (MMR) for optimal context diversity.
        """
        logger.info("multi_query_retrieval_started", query_count=len(queries), top_k=top_k, use_mmr=use_mmr)

        raw_hits: list[dict[str, Any]] = []
        # Oversample per variant to give MMR and Reranker an expressive candidate pool
        fetch_limit = top_k * 2 if use_mmr else top_k

        for query in queries:
            hits = await self.retrieve(
                query=query,
                user_id=user_id,
                file_ids=file_ids,
                top_k=fetch_limit,
                score_threshold=score_threshold,
                with_vectors=use_mmr,
            )
            raw_hits.extend(hits)

        merged = self.resolve_children_to_parents(raw_hits)
        merged.sort(key=lambda r: r.get("score", 0.0), reverse=True)

        # Apply Dense MMR if requested and query vector available
        if use_mmr and queries and merged:
            primary_query = queries[0]
            query_vec = await self.generate_embedding(primary_query)

            candidate_vectors: list[list[float]] = []
            valid_candidates: list[dict[str, Any]] = []

            for item in merged:
                # Use item's dense_vector if available
                vec = item.get("dense_vector")
                if vec and isinstance(vec, list):
                    candidate_vectors.append(vec)
                    valid_candidates.append(item)

            if len(candidate_vectors) == len(merged) and len(valid_candidates) > 1:
                diversified = maximal_marginal_relevance(
                    query_vector=query_vec,
                    candidate_vectors=candidate_vectors,
                    candidates=valid_candidates,
                    top_k=top_k,
                    lambda_mult=mmr_lambda,
                )
                logger.info(
                    "mmr_diversification_applied",
                    candidates_in=len(merged),
                    candidates_out=len(diversified),
                    lambda_mult=mmr_lambda,
                )
                return diversified

        logger.info("multi_query_retrieval_completed", raw_hits=len(raw_hits), resolved=len(merged))
        return merged[:top_k]

    def resolve_children_to_parents(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Child→parent mapping: deduplicates child-chunk hits by their parent,
        keeping the parent's full text so smaller, fuzzier children can still
        recover precise, full-context passages.

        Qdrant returns hits as {id, score, payload}; this flattens the fields the
        reranker and citation formatter depend on (content, file_id, filename,
        chunk_index, metadata) to the candidate top level.
        """
        merged: dict[str, dict[str, Any]] = {}

        for result in results:
            payload = result.get("payload", {})
            parent_id = payload.get("parent_chunk_id")
            parent_content = payload.get("parent_chunk_content")
            # Unique dedup key: parent row id for children, Qdrant point id otherwise
            key = str(parent_id) if parent_id else str(result.get("id"))
            acc = merged.get(key)
            child_score = float(result.get("score", 0.0))

            if acc is None:
                acc = dict(result)
                acc["parent_chunk_id"] = parent_id
                acc["content"] = parent_content or payload.get("content", "")
                acc["file_id"] = payload.get("file_id")
                acc["filename"] = payload.get("filename", "Document")
                acc["chunk_index"] = payload.get("chunk_index", acc.get("chunk_index", 0))
                acc["metadata"] = payload.get("metadata", payload)
                acc["is_child"] = bool(parent_id)
                acc["child_hit_count"] = 1
                acc["child_text"] = payload.get("content", "")
                acc["contextual_prefix"] = payload.get("contextual_prefix")
                acc["score"] = child_score
                if "dense_vector" in result:
                    acc["dense_vector"] = result["dense_vector"]
                merged[key] = acc
            else:
                acc["child_hit_count"] += 1
                if child_score > float(acc.get("score", 0.0)):
                    acc["score"] = child_score
                    acc["child_text"] = payload.get("content", "")
                    acc["contextual_prefix"] = payload.get("contextual_prefix")
                    if "dense_vector" in result:
                        acc["dense_vector"] = result["dense_vector"]

        return list(merged.values())


# Singleton instance for backwards-compatibility (default IVectorStore = QdrantService)
retrieval_service = RetrievalService()


def get_retriever() -> IRetriever:
    """Dependency provider returning the active IRetriever implementation."""
    return retrieval_service
