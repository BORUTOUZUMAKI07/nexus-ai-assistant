"""
RAG Application Service.
Owns the end-to-end RAG query workflow (SRP):
Query Rewriting (+ conditional HyDE) -> Multi-Query Hybrid Search -> Child→Parent
Resolution -> Cross-Encoder / FlashRank Rerank -> Answer-Coverage Grading ->
Citation & Context Generation.

Route handlers depend on this abstraction, never directly on retrieval,
reranker, rewriter, or citation singletons (DIP).
"""
from typing import Any
from uuid import UUID

import structlog
from backend.app.core.config import settings
from backend.app.domain.file.schemas import RAGCitation, RAGQueryResult
from backend.app.services.observability.tracing import trace_span
from backend.app.services.rag.answer_coverage import grade_answer_coverage
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
        decision_client: object | None = None,
    ) -> None:
        self._retriever = retriever
        self._reranker = reranker
        self._rewriter = rewriter
        self._citation = citation_service
        # Left as None by default rather than importing ai_client here. The
        # decision layer must not be able to make module import order
        # significant, and an absent client is a meaningful value: answer
        # coverage then declines to grade and every chunk survives, which is the
        # safe direction. Wired at composition time in `_build_default_service`
        # below, and that line is the only thing standing between this feature
        # and a permanent no-op -- asserted by
        # `test_the_default_rag_service_has_a_decision_client_wired`.
        self._decision_client = decision_client

    async def query(
        self,
        *,
        query: str,
        user_id: UUID,
        file_ids: list[UUID] | None = None,
        top_k: int | None = None,
        score_threshold: float = 0.35,
    ) -> RAGQueryResult:
        """
        Execute end-to-end RAG query:
        1. Rewrite query into 2-3 variants (+ conditional HyDE).
        2. Multi-query hybrid search, resolving child hits to parent chunks.
        3. Cross-Encoder reranking via injected IReranker.
        4. Citation formatting via CitationService.
        """
        # Fallback only — every production caller passes top_k explicitly, so
        # this changes no request path. It exists so a new caller that omits the
        # argument inherits settings.RAG_TOP_K instead of a literal in a
        # signature. Note RAG_TOP_K defaults to 20, not the 5 this signature
        # used to hardcode, so the two are not interchangeable: callers that
        # relied on the old default must keep passing top_k explicitly.
        if top_k is None:
            top_k = settings.RAG_TOP_K
        logger.info("rag_service_query_started", query=query, user_id=str(user_id), top_k=top_k)

        async with trace_span("rag_query", {"query": query, "user_id": str(user_id), "top_k": top_k}):
            # 1. Query rewriting (multi-query expansion + conditional HyDE)
            queries = await self._rewriter.rewrite(query)

            # 2. Multi-query hybrid search with Dense MMR (child→parent resolved & diversified)
            candidates = await self._retriever.retrieve_multi(
                queries=queries,
                user_id=user_id,
                file_ids=file_ids,
                top_k=top_k,
                score_threshold=score_threshold,
                use_mmr=True,
                mmr_lambda=0.7,
            )

            # 3. Cross-Encoder / FlashRank Reranking
            reranked = await self._reranker.rerank(
                query=query,
                candidates=candidates,
                top_n=top_k,
            )

            # 3b. Answer coverage. Every stage above ranks by similarity, so a
            # passage about the wrong version of the right thing scores well and
            # reaches the synthesizer as evidence. This is the one check that
            # asks whether a passage states the answer. Off by default; see
            # RAG_ANSWER_COVERAGE_ENABLED for why, and answer_coverage.py for
            # why a chunk is only ever dropped on a real measurement.
            reranked, coverage_report = await grade_answer_coverage(
                query,
                reranked,
                client=self._decision_client,
            )

            # 4. Format citations
            context_text, citations = self._citation.format_citations(reranked)

        logger.info(
            "rag_service_query_completed",
            query_variants=len(queries),
            candidates_retrieved=len(candidates),
            citations_generated=len(citations),
            coverage_dropped=coverage_report["dropped"],
            coverage_measured=coverage_report["measured"],
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


def _build_default_service() -> RAGService:
    """Compose the module-level singleton with a real decision client.

    The client is resolved lazily and defensively for the same reason the HyDE
    rewriter's is (`query_rewriter._get_ai_client`): importing the LiteLLM router
    at module scope would make this module's import order significant, and a
    client that cannot be imported must leave the pipeline running rather than
    break it at startup.
    """
    client: object | None = None
    try:
        from backend.app.infrastructure.ai.litellm_client import ai_client

        client = ai_client
    except Exception as exc:  # pragma: no cover - import-time environment fault
        logger.warning(
            "rag_service_decision_client_unavailable",
            error_type=type(exc).__name__,
        )
    return RAGService(decision_client=client)


# Singleton instance wired with default components (DIP defaults applied in __init__)
rag_service = _build_default_service()
