"""RAG Retrieval Service.
Coordinates dense & sparse embedding generation and dispatches native
Hybrid Search with Reciprocal Rank Fusion (RRF) to Qdrant.
"""
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


class RetrievalService(IRetriever):
    """
    Retrieves most relevant document chunks for a query using Hybrid RRF Search.
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
        Generates dense vector embedding for text using the configured local
        fastembed model. Raises loudly on failure instead of returning a
        synthetic vector — silently indexing garbage poisons retrieval.
        """
        try:
            from fastembed import TextEmbedding

            # Fastembed runs locally on CPU/free without API cost,
            # dimension must match settings.EMBEDDING_DIMENSION / the Qdrant collection.
            model = TextEmbedding(model_name=self.embedding_model)
            embeddings = list(model.embed([text]))
            vector = embeddings[0].tolist()
            if len(vector) != settings.EMBEDDING_DIMENSION:
                raise RuntimeError(
                    f"embedding dimension {len(vector)} != settings.EMBEDDING_DIMENSION "
                    f"({settings.EMBEDDING_DIMENSION}) — collection/ingest mismatch"
                )
            return vector
        except Exception as exc:
            logger.error("embedding_generation_failed", error=str(exc), model=self.embedding_model)
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
        # Stable index across processes (derive from md5 digest, not hash())
        indices = [
            int(hashlib.md5(w.encode("utf-8")).hexdigest()[:8], 16) % 100000
            for w in counts.keys()
        ]
        values = [float(c) for c in counts.values()]
        return {"indices": indices, "values": values}

    async def retrieve(
        self,
        query: str,
        user_id: UUID,
        file_ids: list[UUID] | None = None,
        top_k: int = 5,
        score_threshold: float = 0.35,
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
        )

        return results

    async def retrieve_multi(
        self,
        queries: list[str],
        user_id: UUID,
        file_ids: list[UUID] | None = None,
        top_k: int = 5,
        score_threshold: float = 0.35,
    ) -> list[dict[str, Any]]:
        """
        Multi-query retrieval: runs hybrid search for every variant, then
        resolves child hits back to their parent chunks (deduplicated) for
        full-precision context. Returns the top-k best resolved candidates.
        """
        logger.info("multi_query_retrieval_started", query_count=len(queries), top_k=top_k)

        raw_hits: list[dict[str, Any]] = []
        for query in queries:
            hits = await self.retrieve(
                query=query,
                user_id=user_id,
                file_ids=file_ids,
                top_k=top_k,
                score_threshold=score_threshold,
            )
            raw_hits.extend(hits)

        merged = self.resolve_children_to_parents(raw_hits)
        merged.sort(key=lambda r: r.get("score", 0.0), reverse=True)

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
                merged[key] = acc
            else:
                acc["child_hit_count"] += 1
                if child_score > float(acc.get("score", 0.0)):
                    acc["score"] = child_score
                    acc["child_text"] = payload.get("content", "")
                    acc["contextual_prefix"] = payload.get("contextual_prefix")

        return list(merged.values())


# Singleton instance for backwards-compatibility (default IVectorStore = QdrantService)
retrieval_service = RetrievalService()


def get_retriever() -> IRetriever:
    """Dependency provider returning the active IRetriever implementation."""
    return retrieval_service
