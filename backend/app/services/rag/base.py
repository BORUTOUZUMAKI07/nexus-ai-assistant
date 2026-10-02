"""
Abstract RAG Component Interfaces.
Enforces Interface Segregation and Open/Closed principles across RAG pipeline modules.
"""
from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

#: Key a retriever sets on a result whose ``content`` is a preview rather than
#: the source body. Declared here, on the contract, because the consumer that
#: has to act on it (the researcher deciding whether to fetch the full page) is
#: not the component that produced it.
REQUIRES_SCRAPING = "requires_scraping"


def mark_requires_scraping(result: dict[str, Any], reason: str = "") -> dict[str, Any]:
    """Flag a retrieval result as needing its full source fetched.

    Why this exists: the retrieval providers do not return the same amount of
    text. Tavily hands back a snippet capped at ``SEARCH_SNIPPET_LIMIT``, and
    DuckDuckGo hands back whatever the result page exposed. A caller that
    cannot tell a preview from a full body has to either always scrape (wasteful)
    or never scrape (sometimes answers from a 200-character snippet). Declaring
    the difference on the result removes the guess.
    """
    result[REQUIRES_SCRAPING] = True
    if reason:
        result["scrape_reason"] = reason
    return result


def needs_scraping(result: dict[str, Any]) -> bool:
    """Whether a result is a preview the caller should fetch in full.

    Defaults to True for a result that carries a URL but no usable body: an
    unlabelled empty result is far more often a preview than a genuinely
    content-free page, and the cost of a needless scrape is far lower than the
    cost of answering from a snippet.
    """
    if REQUIRES_SCRAPING in result:
        return bool(result[REQUIRES_SCRAPING])
    if not result.get("url"):
        return False
    content = result.get("content") or result.get("snippet") or ""
    return len(str(content)) < FULL_TEXT_CHARS


#: Below this, a body is treated as a preview rather than a full page.
FULL_TEXT_CHARS = 600


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
    async def rewrite(self, query: str) -> list[str]:
        """
        Expand a user query into 2-3 retrieval variants.
        Optionally Conditionally produce a HyDE hypothesis.

        Async because producing a real HyDE hypothesis is a model call. It was
        sync while the hypothesis was a fixed string template, and became async
        when it stopped being one -- see `RAGService.query`, the only production
        caller on the hot path.
        """
        pass
