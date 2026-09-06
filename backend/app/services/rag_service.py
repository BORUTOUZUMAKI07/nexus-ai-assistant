"""
RAG Application Service.
Owns the end-to-end RAG query workflow (SRP):
Query Rewriting (+ conditional HyDE) -> Multi-Query Hybrid Search -> Child→Parent
Resolution -> Cross-Encoder / FlashRank Rerank -> Citation & Context Generation.

Route handlers depend on this abstraction, never directly on retrieval,
reranker, rewriter, or citation singletons (DIP).
"""
from typing import Any
from uuid import UUID

import structlog
from backend.app.domain.file.schemas import RAGCitation, RAGQueryResult
from backend.app.services.observability.tracing import trace_span
from backend.app.services.rag.base import IReranker, IRetriever, IRewriter
from backend.app.services.rag.citation import CitationService
from backend.app.services.rag.citation import (
    citation_service as _default_citation_service,
)
from backend.app.services.rag.query_rewriter import (
    query_rewriter_service as _default_rewriter,
)
from backend.app.services.rag.reranking import reranker as _default_reranker
from backend.app.services.rag.retrieval import (
    retrieval_service as _default_retrieval_service,
)

logger = structlog.get_logger(__name__)


class RAGService:
    """
    Orchestrates the retrieval-augmented generation pipeline.
    Injected with IRetriever, IReranker, IRewriter, and CitationService (DIP).
    """

    def __init__(
        self,
        retriever: IRetriever = _default_retrieval_service,
        reranker: IReranker = _default_reranker,
        rewriter: IRewriter = _default_rewriter,
        citation_service: CitationService = _default_citation_service,
    ) -> None:
        self._retriever = retriever
        self._reranker = reranker
        self._rewriter = rewriter
        self._citation = citation_service

    async def query(
        self,
        *,
        query: str,
        user_id: UUID,
        file_ids: list[UUID] | None = None,
        top_k: int = 5,
        score_threshold: float = 0.35,
    ) -> RAGQueryResult:
        """
        Execute end-to-end RAG query:
        1. Rewrite query into 2-3 variants (+ conditional HyDE).
        2. Multi-query hybrid search, resolving child hits to parent chunks.
        3. Cross-Encoder reranking via injected IReranker.
        4. Citation formatting via CitationService.
        """
        logger.info("rag_service_query_started", query=query, user_id=str(user_id), top_k=top_k)

        async with trace_span("rag_query", {"query": query, "user_id": str(user_id), "top_k": top_k}):
            # 1. Query rewriting (multi-query expansion + conditional HyDE)
            queries = self._rewriter.rewrite(query)

            # 2. Multi-query hybrid search (child→parent resolved)
            candidates = await self._retriever.retrieve_multi(
                queries=queries,
                user_id=user_id,
                file_ids=file_ids,
                top_k=top_k,
                score_threshold=score_threshold,
            )

            # 3. Cross-Encoder / FlashRank Reranking
            reranked = await self._reranker.rerank(
                query=query,
                candidates=candidates,
                top_n=top_k,
            )

            # 4. Format citations
            context_text, citations = self._citation.format_citations(reranked)

        logger.info(
            "rag_service_query_completed",
            query_variants=len(queries),
            candidates_retrieved=len(candidates),
            citations_generated=len(citations),
        )

        return RAGQueryResult(
            query=query,
            citations=citations,
            total_retrieved=len(citations),
            query_variants=queries,
        )

    def format_citations_and_context(
        self,
        chunks: list[dict[str, Any]],
    ) -> tuple[str, list[RAGCitation]]:
        """Format retrieved chunks into prompt context and structured citations."""
        return self._citation.format_citations(chunks)

    def verify_grounding(
        self,
        response_text: str,
        citations: list[RAGCitation],
    ) -> float:
        """Compute grounding verification score for an LLM response."""
        return self._citation.verify_grounding(response_text, citations)


# Singleton instance wired with default components (DIP defaults applied in __init__)
rag_service = RAGService()
