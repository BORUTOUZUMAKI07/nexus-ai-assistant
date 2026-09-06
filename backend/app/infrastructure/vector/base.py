"""
Abstract Vector Store Interface.
Enforces Interface Segregation, Open/Closed, and Dependency Inversion principles for vector search engines.
"""
from abc import ABC, abstractmethod
from typing import Any


class IVectorStore(ABC):
    """
    Abstract contract for vector database providers (Qdrant, Milvus, Chroma, Pinecone).
    """

    @abstractmethod
    async def ensure_collection(self) -> None:
        """Idempotently initialize vector indices and schema."""
        pass

    @abstractmethod
    async def hybrid_search_with_fusion(
        self,
        dense_vector: list[float],
        sparse_indices: list[int],
        sparse_values: list[float],
        limit: int = 5,
        filter_conditions: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute hybrid dense + sparse vector search with reciprocal rank fusion."""
        pass

    async def hybrid_search(
        self,
        dense_vector: list[float],
        sparse_indices: list[int],
        sparse_values: list[float],
        limit: int = 5,
        filter_conditions: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Alias for hybrid_search_with_fusion."""
        return await self.hybrid_search_with_fusion(
            dense_vector, sparse_indices, sparse_values, limit, filter_conditions
        )

    @abstractmethod
    async def upsert_points(self, points: list[dict[str, Any]]) -> None:
        """Batch upsert points containing vectors and payloads."""
        pass

    @abstractmethod
    async def delete_by_filter(self, filter_conditions: dict[str, Any]) -> None:
        """Delete points matching specific key-value conditions."""
        pass
