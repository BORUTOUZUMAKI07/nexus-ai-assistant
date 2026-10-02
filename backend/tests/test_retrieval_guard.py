"""
Unit tests for CRAG-style corrective retrieval (retreiver-grader + re-retrieve).
"""
import uuid

import pytest
from backend.app.domain.file.schemas import RAGCitation, RAGQueryResult
from backend.app.services.rag.critique import (
    VERDICT_INSUFFICIENT,
    VERDICT_RELEVANT,
    VERDICT_UNRELATED,
    retrieval_critique_service,
)
from backend.app.services.rag.retrieval_guard import RetrievalGuardService


def _citation(score: float, snippet: str = "evidence content") -> RAGCitation:
    return RAGCitation(
        file_id=uuid.uuid4(),
        filename="doc.md",
        chunk_index=0,
        score=score,
        content_snippet=snippet,
    )


class FakeRewriter:
    def __init__(self, variants: list[str]):
        self.variants = variants

    async def rewrite(self, query: str) -> list[str]:
        # Async because IRewriter.rewrite became async when HyDE hypothesis
        # generation became a model call; the corrective re-query awaits it.
        return self.variants


class FakeRAG:
    """Re-query returns a scripted sequence of RAGQueryResult payloads."""

    def __init__(self, *results: list[RAGCitation]):
        self.chunks: list[list[RAGCitation]] = list(results)
        self.calls: list[str] = []

    async def query(self, **kwargs) -> RAGQueryResult:
        self.calls.append(str(kwargs.get("query", "")))
        citations = self.chunks.pop(0) if self.chunks else []
        return RAGQueryResult(
            query=str(kwargs.get("query", "")),
            citations=citations,
            total_retrieved=len(citations),
        )


@pytest.mark.asyncio
async def test_relevant_grade_is_never_requeried():
    rag = FakeRAG()
    guard = RetrievalGuardService(rewriter=FakeRewriter(["variant"]), max_revisions=1)
    citations = [_citation(0.8)]

    result = await guard.correct_retrieval(
        query="q", user_id=uuid.uuid4(), citations=citations,
        verdict=VERDICT_RELEVANT, rag=rag,
    )
    assert result["action"] == "none"
    assert rag.calls == []
    assert result["verdict"] == VERDICT_RELEVANT


@pytest.mark.asyncio
async def test_insufficient_grade_re_retrieves_and_upgrades_to_relevant():
    weak = [_citation(0.20)]
    strong = [_citation(0.85)]
    rag = FakeRAG(strong)
    guard = RetrievalGuardService(rewriter=FakeRewriter(["refined query"]), max_revisions=1)

    result = await guard.correct_retrieval(
        query="original query", user_id=uuid.uuid4(), citations=weak,
        verdict=VERDICT_INSUFFICIENT, rag=rag,
    )
    assert result["action"] == "re-retrieved"
    assert result["verdict"] == VERDICT_RELEVANT
    assert result["revision_count"] == 1
    assert result["refined_queries"] == ["refined query"]
    assert len(result["citations"]) == 1 and result["citations"][0].score == 0.85
    assert rag.calls == ["refined query"]


@pytest.mark.asyncio
async def test_insufficient_grade_with_no_improvement_keeps_original():
    weak = [_citation(0.20)]
    equally_weak = [_citation(0.18)]
    rag = FakeRAG(equally_weak)
    guard = RetrievalGuardService(rewriter=FakeRewriter(["refined query"]), max_revisions=1)

    result = await guard.correct_retrieval(
        query="q", user_id=uuid.uuid4(), citations=weak,
        verdict=VERDICT_INSUFFICIENT, rag=rag,
    )
    assert result["action"] == "none"
    assert result["verdict"] == VERDICT_INSUFFICIENT
    # Original citations retained (correction only accepted when strictly better).
    assert result["citations"][0] is weak[0]


@pytest.mark.asyncio
async def test_revision_budget_is_bounded():
    weak = [_citation(0.20)]
    still_weak = [_citation(0.22)]
    rag = FakeRAG(still_weak, still_weak, still_weak)
    guard = RetrievalGuardService(rewriter=FakeRewriter(["v1", "v2", "v3"]), max_revisions=2)

    result = await guard.correct_retrieval(
        query="q", user_id=uuid.uuid4(), citations=weak,
        verdict=VERDICT_INSUFFICIENT, rag=rag,
    )
    # A strictly-better-but-still-weak result only consumes a single revision;
    # with max_revisions=2 at most 2 corrective queries run.
    assert result["revision_count"] <= 2
    assert len(rag.calls) <= 2


@pytest.mark.asyncio
async def test_unrelated_without_citations_is_noop():
    rag = FakeRAG()
    guard = RetrievalGuardService(rewriter=FakeRewriter(["v"]), max_revisions=1)
    result = await guard.correct_retrieval(
        query="q", user_id=uuid.uuid4(), citations=[],
        verdict=VERDICT_UNRELATED, rag=rag,
    )
    assert result["action"] == "none"
    assert rag.calls == []


@pytest.mark.asyncio
async def test_rag_failure_falls_back_fail_open():
    class BrokenRAG:
        async def query(self, **kwargs):  # pragma: no cover - intentional failure
            raise RuntimeError("qdrant down")

    guard = RetrievalGuardService(rewriter=FakeRewriter(["v"]), max_revisions=1)
    result = await guard.correct_retrieval(
        query="q", user_id=uuid.uuid4(),
        citations=[_citation(0.2)], verdict=VERDICT_INSUFFICIENT,
        rag=BrokenRAG(),
    )
    assert result["action"] == "none"
    assert result["verdict"] == VERDICT_INSUFFICIENT


def test_default_singleton_uses_real_critique():
    # Sanity: the wired singleton grades with the production thresholds.
    verdict, score = retrieval_critique_service.grade([_citation(0.9)])
    assert verdict == VERDICT_RELEVANT
    assert score > 0.3
