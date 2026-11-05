"""Relevance filtering in the retrieval pipeline.

Two separate floors exist and they are on two separate score scales. These
tests pin both, because getting the scale wrong is silent: the pipeline keeps
returning chunks and only the *quality* of the citations changes.

Found live: a reply of "FAST-OK" carried a citation whose score was 0.00002 -
an unrelated chunk in the same knowledge base. `top_n=5` was treated as a
promise of five chunks rather than a budget, so after reranking nothing removed
a passage the cross-encoder had scored at effectively zero.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from backend.app.core.config import settings
from backend.app.domain.file.schemas import RAGCitation
from backend.app.infrastructure.vector.qdrant_client import QdrantService
from backend.app.services.rag import reranking
from backend.app.services.rag.citation import citation_service
from backend.app.services.rag.reranking import RerankingService


class _FakeRanker:
    """Scores by text so a test can state relevance directly."""

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores

    def rerank(self, request):  # noqa: ANN001 - mirrors FlashRank's signature
        passages = request.passages if hasattr(request, "passages") else []
        results = [
            {"meta": p["meta"], "score": self._scores.get(p["text"], 0.0)}
            for p in passages
        ]
        return sorted(results, key=lambda r: r["score"], reverse=True)


def _chunk(text: str) -> dict:
    return {"content": text, "filename": "doc.md", "chunk_index": 0}


@pytest.mark.asyncio
async def test_rerank_drops_chunks_below_the_relevance_floor(monkeypatch):
    """An unrelated chunk must not reach the synthesizer as evidence."""
    candidates = [_chunk("release train ships every second Tuesday"), _chunk("unrelated boilerplate")]
    scores = {
        "release train ships every second Tuesday": 0.6474,
        "unrelated boilerplate": 0.0000206,
    }
    monkeypatch.setattr(reranking, "_get_ranker", lambda _model: _FakeRanker(scores))

    out = await RerankingService().rerank(query="when does the train ship?", candidates=candidates, top_n=5)

    assert [c["content"] for c in out] == ["release train ships every second Tuesday"]
    assert out[0]["rerank_score"] == pytest.approx(0.6474)


@pytest.mark.asyncio
async def test_relevance_floor_is_configurable_and_can_be_disabled(monkeypatch):
    """RAG_MIN_RERANK_SCORE=0 restores keep-everything, which is the escape hatch."""
    candidates = [_chunk("good"), _chunk("bad")]
    monkeypatch.setattr(
        reranking, "_get_ranker", lambda _model: _FakeRanker({"good": 0.9, "bad": 0.01})
    )
    monkeypatch.setattr(settings, "RAG_MIN_RERANK_SCORE", 0.0)

    out = await RerankingService().rerank(query="q", candidates=candidates, top_n=5)

    assert len(out) == 2


@pytest.mark.asyncio
async def test_rerank_returns_empty_rather_than_junk():
    """No evidence is a valid answer; a fabricated citation is not."""
    out = await RerankingService().rerank(query="q", candidates=[], top_n=5)
    assert out == []


@pytest.mark.asyncio
async def test_dense_threshold_is_applied_before_fusion(monkeypatch):
    """The cutoff is a cosine floor on the dense prefetch, not an RRF cutoff.

    Qdrant's fused scores are 1/(2+rank) - 0.5, 0.333, 0.25 for ranks 1-3 - so
    thresholding them at 0.35 was a rank cutoff in disguise. Here the fused
    score of 0.167 (rank 5) must survive, because relevance was already decided
    by the cosine floor inside the prefetch.
    """
    service = QdrantService()
    service.client = AsyncMock()
    service.client.query_points = AsyncMock(
        return_value=SimpleNamespace(
            points=[SimpleNamespace(id="p1", score=0.1666667, payload={"content": "x"}, vector=None)]
        )
    )

    hits = await service.hybrid_search_with_fusion(
        dense_vector=[0.0] * 768,
        sparse_indices=[1],
        sparse_values=[1.0],
        limit=5,
        score_threshold=0.35,
    )

    assert [h["id"] for h in hits] == ["p1"], "fused scores must not be filtered by a cosine cutoff"

    kwargs = service.client.query_points.await_args.kwargs
    dense_prefetch, sparse_prefetch = kwargs["prefetch"]
    assert dense_prefetch.score_threshold == 0.35
    assert sparse_prefetch.score_threshold is None, "BM25 scores are unbounded; lexical hits stay"


# ── Grounding badge ───────────────────────────────────────────────────────────


def _cites() -> list[RAGCitation]:
    return [
        RAGCitation(file_id=uuid4(), filename="live-verify.md", chunk_index=0, score=1.0,
                    content_snippet="The launch code for Project Halcyon is ORBIT-77.", metadata={})
    ]


def test_grounding_recognises_model_native_marker_style():
    """`【1†file】` is a citation, not a missing one.

    Live: an answer citing a 100%-relevance chunk was badged "LOW EVIDENCE 30%"
    only because the model wrote the marker in its own bracket style.
    """
    text = "The launch code for Project Halcyon is ORBIT-77【1†live-verify.md】."
    assert citation_service.verify_grounding(text, _cites()) >= 0.9


def test_grounding_still_rewards_the_plain_marker():
    assert citation_service.verify_grounding("It is ORBIT-77 [1].", _cites()) >= 0.9


def test_grounding_stays_low_without_any_citation():
    assert citation_service.verify_grounding("It is ORBIT-77.", _cites()) == 0.3
