"""
Qdrant Vector Database Client.
Provides async hybrid search (dense + sparse with RRF fusion),
collection initialization with payload indexes, and batch upsert.

Reference: https://qdrant.tech/documentation/concepts/hybrid-queries/
"""
from typing import Any

import structlog
from backend.app.core.config import settings
from backend.app.infrastructure.vector.base import IVectorStore
from qdrant_client import AsyncQdrantClient, models

logger = structlog.get_logger(__name__)

COLLECTION_NAME = "nexus_knowledge"
VECTOR_SIZE = 384  # BAAI/bge-small-en-v1.5 dimensions


class QdrantService(IVectorStore):
    def __init__(self) -> None:
        if settings.QDRANT_API_KEY:
            self.client = AsyncQdrantClient(
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY,
            )
        else:
            self.client = AsyncQdrantClient(url=settings.QDRANT_URL)

    async def ensure_collection(self) -> None:
        """
        Idempotently creates the hybrid collection with:
          - Dense HNSW vectors (cosine, INT8 quantization)
          - Sparse vectors for BM25 keyword recall
          - Payload indexes for fast user/file pre-filtering
        """
        collections = await self.client.get_collections()
        exists = any(c.name == COLLECTION_NAME for c in collections.collections)

        if not exists:
            await self.client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config={
                    "dense": models.VectorParams(
                        size=VECTOR_SIZE,
                        distance=models.Distance.COSINE,
                    )
                },
                sparse_vectors_config={
                    "sparse": models.SparseVectorParams(
                        index=models.SparseIndexParams(on_disk=False)
                    )
                },
                quantization_config=models.ScalarQuantization(
                    scalar=models.ScalarQuantizationConfig(
                        type=models.ScalarType.INT8,
                        quantile=0.99,
                        always_ram=True,
                    )
                ),
            )
            # Payload indexes for fast pre-filtering
            await self.client.create_payload_index(
                COLLECTION_NAME, "user_id", models.PayloadSchemaType.KEYWORD
            )
            await self.client.create_payload_index(
                COLLECTION_NAME, "file_id", models.PayloadSchemaType.KEYWORD
            )
            await self.client.create_payload_index(
                COLLECTION_NAME, "file_type", models.PayloadSchemaType.KEYWORD
            )
            logger.info("qdrant_collection_initialized", collection=COLLECTION_NAME)
        else:
            logger.debug("qdrant_collection_already_exists", collection=COLLECTION_NAME)

    async def hybrid_search_with_fusion(
        self,
        dense_vector: list[float],
        sparse_indices: list[int],
        sparse_values: list[float],
        limit: int = 5,
        filter_conditions: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Executes native hybrid search with multi-stage Prefetch and
        server-side Reciprocal Rank Fusion (RRF).

        Reference: https://qdrant.tech/documentation/concepts/hybrid-queries/
        """
        must_filters: list[models.Condition] = []

        if filter_conditions:
            user_id = filter_conditions.get("user_id")
            if user_id:
                must_filters.append(
                    models.FieldCondition(
                        key="user_id", match=models.MatchValue(value=user_id)
                    )
                )
            file_ids = filter_conditions.get("file_id")
            if file_ids:
                must_filters.append(
                    models.FieldCondition(
                        key="file_id", match=models.MatchAny(any=file_ids)
                    )
                )

        payload_filter = models.Filter(must=must_filters) if must_filters else None

        prefetch_queries = [
            models.Prefetch(
                query=dense_vector,
                using="dense",
                filter=payload_filter,
                limit=limit * 3,
            ),
            models.Prefetch(
                query=models.SparseVector(
                    indices=sparse_indices, values=sparse_values
                ),
                using="sparse",
                filter=payload_filter,
                limit=limit * 3,
            ),
        ]

        # Server-side RRF fusion
        search_results = await self.client.query_points(
            collection_name=COLLECTION_NAME,
            prefetch=prefetch_queries,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True,
        )

        return [
            {
                "id": str(point.id),
                "score": point.score,
                "payload": point.payload or {},
            }
            for point in search_results.points
        ]

    async def upsert_points(self, points: list[dict[str, Any]]) -> None:
        """
        Batch-upserts vector points. Each point dict should have:
          { "id": str, "vector": {"dense": [...], "sparse": {...}}, "payload": {...} }
        """
        qdrant_points = []
        for p in points:
            vector = p.get("vector", {})
            sparse_raw = vector.get("sparse", {})
            qdrant_points.append(
                models.PointStruct(
                    id=p["id"],
                    vector={
                        "dense": vector.get("dense", []),
                        "sparse": models.SparseVector(
                            indices=sparse_raw.get("indices", []),
                            values=sparse_raw.get("values", []),
                        ),
                    },
                    payload=p.get("payload", {}),
                )
            )

        await self.client.upsert(
            collection_name=COLLECTION_NAME,
            points=qdrant_points,
        )
        logger.debug("qdrant_points_upserted", count=len(qdrant_points))

    async def delete_by_filter(self, filter_conditions: dict[str, Any]) -> None:
        """Deletes all points matching a filter — used when a file is deleted."""
        must_filters = [
            models.FieldCondition(key=k, match=models.MatchValue(value=v))
            for k, v in filter_conditions.items()
        ]
        await self.client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=models.FilterSelector(
                filter=models.Filter(must=must_filters)
            ),
        )
        logger.info("qdrant_points_deleted", filter=filter_conditions)


# Singleton instance for backwards-compatibility
vector_db = QdrantService()


def get_vector_store() -> IVectorStore:
    """Dependency provider returning the active IVectorStore implementation."""
    return vector_db
