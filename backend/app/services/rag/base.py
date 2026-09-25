"""
Abstract RAG Component Interfaces.
Enforces Interface Segregation and Open/Closed principles across RAG pipeline modules.
"""
from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID


class IChunker(ABC):
    """Abstract document chunking contract."""

    @abstractmethod
    def chunk_document(self, text: str, source_metadata: dict[str, Any]) -> list[Any]:
        """Split text into structured chunks with metadata."""
        pass


class IReranker(ABC):
    """Abstract cross-encoder reranker contract."""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_n: int = 5,
    ) -> list[dict[str, Any]]:
        """Rerank candidate passages by relevance to the query."""
        pass


class IRetriever(ABC):
    """Abstract hybrid vector/sparse retrieval contract."""

    async def generate_embedding(self, text: str) -> list[float]:
        """Generate dense vector embedding for a single text."""
        vectors = await self.generate_embeddings_batch([text])
        return vectors[0]

    async def generate_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate dense vector embeddings for multiple texts."""
        return [await self.generate_embedding(t) for t in texts]

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        user_id: UUID,
        file_ids: list[UUID] | None = None,
        top_k: int = 5,
        score_threshold: float = 0.35,
    ) -> list[dict[str, Any]]:
        """Retrieve top relevant candidate chunks."""
        pass

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
        """Retrieve and merge candidates across multiple query variants."""
        merged: list[dict[str, Any]] = []
        for query in queries:
            merged.extend(
                await self.retrieve(
                    query=query,
                    user_id=user_id,
                    file_ids=file_ids,
                    top_k=top_k,
                    score_threshold=score_threshold,
                )
            )
        return merged


class IRewriter(ABC):
    """Abstract query rewriting / expansion contract."""

    @abstractmethod
    def rewrite(self, query: str) -> list[str]:
        """
        Expand a user query into 2-3 retrieval variants.
        Optionally Conditionally produce a HyDE hypothesis.
        """
        pass
