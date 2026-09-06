"""
Tests for the new RAG architecture:
  - Parent-Child chunking (512t parents / 128t children, 32t overlap) + relational linking
  - Anthropic Contextual Prefix generation
  - Multi-query rewriting + conditional HyDE
  - Tavily → Firecrawl → DuckDuckGo search priority ladder
  - Critic/Grader verdict labels
  - CRAG loop (insufficient/unrelated → web search → synthesis)
"""
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from backend.app.domain.file.schemas import RAGCitation, RAGQueryResult
from backend.app.services.rag.base import IReranker, IRetriever, IRewriter
from backend.app.services.rag.chunking import DocumentChunk, chunking_service
from backend.app.services.rag.citation import citation_service
from backend.app.services.rag.critique import (
    VERDICT_INSUFFICIENT,
    VERDICT_RELEVANT,
    VERDICT_UNRELATED,
    RetrievalCritiqueService,
)
from backend.app.services.rag.query_rewriter import QueryRewriterService
from backend.app.services.rag.retrieval import RetrievalService
from backend.app.services.rag_service import RAGService
from backend.app.services.tools.web_search import WebSearchService

rewriter = QueryRewriterService()
critique = RetrievalCritiqueService()


def _make_doc(words: int = 900) -> str:
    """Sentence-per-word document so parent chunking hits sentence boundaries."""
    filler = " ".join(f"Token{i} token{i}a token{i}b." for i in range(words))
    return f"# Introduction\n{filler}"


def _citation(score: float) -> RAGCitation:
    return RAGCitation(
        file_id=uuid.uuid4(),
        filename="doc.txt",
        chunk_index=0,
        score=score,
        content_snippet="proxy evidence snippet",
    )


# ─── 1. Parent-Child Chunking ─────────────────────────────────────────────────


def test_parent_child_structure_and_linking():
    groups = chunking_service.chunk_with_children(
        _make_doc(900),
        source_metadata={"filename": "guide.md", "title": "Nexus Guide"},
    )
    assert len(groups) >= 2  # 900-word doc should yield multiple 512t parents

    for group in groups:
        parent: DocumentChunk = group["parent"]
        assert parent.is_parent is True
        assert parent.parent_chunk_index is None
        assert parent.token_count <= 512 + 5  # parent is ~512 tokens

        for child in group["children"]:
            assert child.is_parent is False
            assert child.parent_chunk_index == parent.chunk_index  # relational link
            assert child.token_count <= 128 + 5  # child is ~128 tokens


def test_child_overlap_is_32_tokens():
    groups = chunking_service.chunk_with_children(_make_doc(600), source_metadata={"filename": "g.md"})
    # Take the first group with at least two children
    for group in groups:
        children = group["children"]
        if len(children) >= 2:
            t0 = children[0].content.split()
            t1 = children[1].content.split()
            assert t0[-32:] == t1[:32], "children should share a 32-token overlap window"
            return
    pytest.fail("expected at least one parent with multiple children")


def test_single_short_parent_still_gets_one_child():
    groups = chunking_service.chunk_with_children(
        "# Intro\nShort section of text.",
        source_metadata={"filename": "s.txt"},
    )
    assert groups
    assert len(groups[0]["children"]) >= 1


# ─── 2. Contextual Prefixes ───────────────────────────────────────────────────


def test_contextual_prefix_attached_to_children_only():
    groups = chunking_service.chunk_with_children(
        "# Architecture\nNexus uses LangGraph for orchestration.\n",
        source_metadata={"filename": "arch.md", "title": "Nexus Architecture"},
    )
    prefix = groups[0]["children"][0].contextual_prefix
    assert prefix is not None
    assert "Nexus Architecture" in prefix  # document title
    assert "Architecture" in prefix  # heading hierarchy
    assert groups[0]["parent"].contextual_prefix is None  # parents carry no prefix


def test_prefix_skips_empty_parts():
    assert chunking_service.generate_contextual_prefix(title="T", header_hierarchy=None) == "T"
    assert chunking_service.generate_contextual_prefix(title="", header_hierarchy="H") == "H"
    assert chunking_service.generate_contextual_prefix(title=None, header_hierarchy=None) == ""


# ─── 3. Multi-Query + Conditional HyDE ────────────────────────────────────────


def test_multi_query_expansion_is_deterministic():
    variants = rewriter.generate_multi_queries("Qdrant hybrid search")
    assert 2 <= len(variants) <= 3
    assert variants[0] == "Qdrant hybrid search"  # fidelity anchor first
    assert len(set(v.lower() for v in variants)) == len(variants)  # no duplicates


def test_hyde_generated_for_short_abstract_query():
    variants = rewriter.rewrite("How does RAG work?")
    assert any("overview of" in v for v in variants)


def test_hyde_skipped_for_keyword_code_query():
    variants = rewriter.rewrite("import FastAPI from fastapi")
    assert not any("overview of" in v for v in variants)
    assert rewriter.should_generate_hyde("DELETE FROM users WHERE id = 5") is False
    assert rewriter.should_generate_hyde("src/app/main.py") is False


def test_hyde_conditional_edge_cases():
    assert rewriter.should_generate_hyde("Qdrant") is True  # tiny natural query
    assert rewriter.should_generate_hyde("") is False
    assert rewriter.should_generate_hyde("SSH") is True  # short query still benefits
    assert rewriter.should_generate_hyde("rm -rf /tmp/cache") is False  # command-style


# ─── 4. Tavily → Firecrawl → DuckDuckGo Ladder ────────────────────────────────


def _search_service(**kwargs) -> WebSearchService:
    """Construct a WebSearchService bypassing settings (empty-string keys = unconfigured)."""
    return WebSearchService(
        tavily_key=kwargs.get("tavily_key") or "",
        firecrawl_key=kwargs.get("firecrawl_key") or "",
    )


def _tool_result(provider: str) -> list[dict[str, Any]]:
    return [{"title": provider, "url": f"https://{provider}.example", "snippet": "s", "content": "c"}]


async def _empty(*_args, **_kwargs) -> list[dict[str, Any]]:
    return []


@pytest.mark.asyncio
async def test_tavily_is_primary_when_configured(monkeypatch):
    svc = _search_service(tavily_key="tv-key", firecrawl_key="fc-key")
    calls: list[str] = []

    async def fake_tavily(self, query, max_results):
        calls.append("tavily")
        return _tool_result("tavily")

    monkeypatch.setattr(WebSearchService, "_search_tavily", fake_tavily)
    results = await svc.search("test query", max_results=5)
    assert calls == ["tavily"]
    assert results[0]["title"] == "tavily"


@pytest.mark.asyncio
async def test_falls_to_firecrawl_when_tavily_empty(monkeypatch):
    svc = _search_service(tavily_key="tv-key", firecrawl_key="fc-key")
    calls: list[str] = []

    async def fake_tavily(self, query, max_results):
        calls.append("tavily")
        return []

    async def fake_firecrawl(self, query, max_results):
        calls.append("firecrawl")
        return _tool_result("firecrawl")

    monkeypatch.setattr(WebSearchService, "_search_tavily", fake_tavily)
    monkeypatch.setattr(WebSearchService, "_search_firecrawl", fake_firecrawl)
    results = await svc.search("test query", max_results=5)
    assert calls == ["tavily", "firecrawl"]
    assert results[0]["title"] == "firecrawl"


@pytest.mark.asyncio
async def test_falls_to_duckduckgo_when_tavily_errors(monkeypatch):
    svc = _search_service(tavily_key="tv-key", firecrawl_key=None)
    calls: list[str] = []

    async def fake_tavily(self, query, max_results):
        calls.append("tavily")
        raise RuntimeError("tavily down")

    async def fake_ddg(self, query, max_results):
        calls.append("duckduckgo")
        return _tool_result("ddg")

    monkeypatch.setattr(WebSearchService, "_search_tavily", fake_tavily)
    monkeypatch.setattr(WebSearchService, "_search_duckduckgo", fake_ddg)
    results = await svc.search("test query", max_results=5)
    assert calls == ["tavily", "duckduckgo"]
    assert results[0]["title"] == "ddg"


@pytest.mark.asyncio
async def test_duckduckgo_only_when_no_keys(monkeypatch):
    svc = _search_service(tavily_key=None, firecrawl_key=None)

    async def fake_ddg(self, query, max_results):
        return _tool_result("ddg")

    monkeypatch.setattr(WebSearchService, "_search_duckduckgo", fake_ddg)
    results = await svc.search("test query", max_results=5)
    assert results[0]["title"] == "ddg"


# ─── 5. Critic / Grader Verdicts ──────────────────────────────────────────────


def test_critic_no_citations_is_unrelated():
    verdict, score = critique.grade([])
    assert verdict == VERDICT_UNRELATED
    assert score == 0.0


def test_critic_strong_evidence_is_relevant():
    verdict, score = critique.grade([_citation(0.6), _citation(0.55)])
    assert verdict == VERDICT_RELEVANT
    assert score >= 0.5


def test_critic_weak_evidence_is_insufficient():
    verdict, _ = critique.grade([_citation(0.2)])
    assert verdict == VERDICT_INSUFFICIENT


def test_critic_noise_is_unrelated():
    verdict, _ = critique.grade([_citation(0.05)])
    assert verdict == VERDICT_UNRELATED


# ─── 6. Child→Parent Resolution ───────────────────────────────────────────────


def test_resolve_children_to_parents_dedupes_and_restores_parent_text():
    svc = RetrievalService()
    parent_a = str(uuid.uuid4())
    parent_b = str(uuid.uuid4())
    hits = [
        {"id": "p1", "score": 0.4, "payload": {
            "parent_chunk_id": parent_a, "parent_chunk_content": "full parent A",
            "content": "child a1", "contextual_prefix": "Doc | Intro"}},
        {"id": "p2", "score": 0.6, "payload": {
            "parent_chunk_id": parent_a, "parent_chunk_content": "full parent A",
            "content": "child a2", "contextual_prefix": "Doc | Intro"}},
        {"id": "p3", "score": 0.5, "payload": {
            "parent_chunk_id": parent_b, "parent_chunk_content": "full parent B",
            "content": "child b1"}},
    ]
    merged = svc.resolve_children_to_parents(hits)
    assert len(merged) == 2  # two parents despite three child hits
    by_id = {m["id"]: m for m in merged}
    a = by_id.get("p1")
    assert a["child_hit_count"] == 2
    assert a["content"] == "full parent A"  # parent text restored
    assert a["contextual_prefix"] == "Doc | Intro"
    assert a["is_child"] is True
    assert by_id["p3"]["content"] == "full parent B"


def test_resolved_candidates_build_valid_citations():
    """
    Regression: Qdrant returns hits nested under `payload` (no top-level
    file_id/content/metadata). Resolution must promote those fields so the
    reranker and citation formatter can build valid RAGCitation objects.
    """
    fid = str(uuid.uuid4())
    svc = RetrievalService()
    hits = [
        {"id": "pt-1", "score": 0.55, "payload": {
            "content": "child a1", "contextual_prefix": "Doc | Intro",
            "chunk_index": 5, "file_id": fid, "filename": "guide.md",
            "user_id": str(uuid.uuid4()), "is_parent": False,
            "parent_chunk_id": str(uuid.uuid4()), "parent_chunk_content": "full parent text here",
            "metadata": {"file_id": fid, "filename": "guide.md", "header": "Intro"},
        }},
    ]
    resolved = svc.resolve_children_to_parents(hits)
    assert resolved[0]["file_id"] == fid
    assert resolved[0]["metadata"]["filename"] == "guide.md"

    context_text, citations = citation_service.format_citations(resolved)
    assert len(citations) == 1
    assert str(citations[0].file_id) == fid  # would have raised ValidationError before
    assert citations[0].filename == "guide.md"
    assert "full parent text here" in citations[0].content_snippet
    assert "full parent text here" in context_text


def test_resolve_uses_unique_point_ids_across_files():
    """Flat chunks from two files share chunk_index 0 but must stay distinct."""
    svc = RetrievalService()
    hits = [
        {"id": "pt-a", "score": 0.5, "payload": {
            "content": "chunk in file a", "chunk_index": 0,
            "file_id": str(uuid.uuid4()), "filename": "a.md", "user_id": "u1"}},
        {"id": "pt-b", "score": 0.5, "payload": {
            "content": "chunk in file b", "chunk_index": 0,
            "file_id": str(uuid.uuid4()), "filename": "b.md", "user_id": "u1"}},
    ]
    merged = svc.resolve_children_to_parents(hits)
    assert len(merged) == 2  # must NOT collapse on chunk_index==0
    assert {m["filename"] for m in merged} == {"a.md", "b.md"}


def test_sparse_vector_indices_are_stable_across_processes():
    """
    Builtin `hash()` is randomized per-process; the sparse index must NOT be,
    or ingest-time and query-time indices would never match.
    """
    import hashlib

    svc = RetrievalService()
    first = svc.generate_sparse_vector("RAG hybrid search RAG")
    second = svc.generate_sparse_vector("RAG hybrid search RAG")
    assert first == second  # deterministic within a process

    # Cross-process guarantee: index is plain md5, not Python's randomized hash
    index_for_rag = int(hashlib.md5(b"rag").hexdigest()[:8], 16) % 100000
    assert index_for_rag in first["indices"]


@pytest.mark.asyncio
async def test_retrieve_multi_merges_and_caps_top_k(monkeypatch):
    svc = RetrievalService()

    async def fake_retrieve(self, query, user_id, file_ids=None, top_k=5, score_threshold=0.35):
        return [{"id": query, "score": 0.7, "payload": {"chunk_index": 0, "content": query}}]

    monkeypatch.setattr(RetrievalService, "retrieve", fake_retrieve)
    results = await svc.retrieve_multi(
        queries=["a", "b", "c"], user_id=uuid.uuid4(), top_k=2
    )
    assert len(results) == 2  # capped to top_k after merging three queries


# ─── 7. RAGService wiring (rewriter → multi-query → rerank → citations) ───────


class _FakeRewriter(IRewriter):
    def __init__(self):
        self.called = False

    def rewrite(self, query: str) -> list[str]:
        self.called = True
        return [query, f"{query} rephrased"]


class _FakeRetriever(IRetriever):
    def __init__(self):
        self.queries: list[str] = []

    async def retrieve(self, *args, **kwargs):
        raise NotImplementedError

    async def retrieve_multi(self, queries, user_id, file_ids=None, top_k=5, score_threshold=0.35):
        self.queries = list(queries)
        return [
            {"file_id": str(user_id), "chunk_index": 0, "content": "Evidence text.",
             "score": 0.8, "metadata": {"filename": "doc.txt", "file_id": str(user_id)}}
        ]


class _FakeReranker(IReranker):
    async def rerank(self, query, candidates, top_n=5):
        return [dict(c, rerank_score="0.9") for c in candidates]


@pytest.mark.asyncio
async def test_rag_service_uses_rewriter_and_multi_query_retrieval():
    rewriter_fake = _FakeRewriter()
    retriever_fake = _FakeRetriever()
    svc = RAGService(
        retriever=retriever_fake,
        reranker=_FakeReranker(),
        rewriter=rewriter_fake,
        citation_service=citation_service,
    )
    result = await svc.query(query="What is chunking?", user_id=uuid.uuid4(), top_k=5)

    assert rewriter_fake.called is True
    assert retriever_fake.queries == ["What is chunking?", "What is chunking? rephrased"]
    assert result.query_variants == retriever_fake.queries
    assert result.total_retrieved == 1
    assert result.citations[0].filename == "doc.txt"


# ─── 8. CRAG Loop (critic node routing) ───────────────────────────────────────


def _node_state(**overrides) -> dict[str, Any]:
    state = {
        "user_id": str(uuid.uuid4()),
        "messages": [MagicMock()],
    }
    state["messages"][0].content = "Tell me about RAG"
    state.update(overrides)
    return state


@pytest.mark.asyncio
async def test_critic_node_queues_web_search_on_insufficient_evidence(monkeypatch):
    from backend.app.agents.orchestrator import nodes

    class _FakeRAG:
        async def query(self, **kwargs):
            return RAGQueryResult(query="q", citations=[_citation(0.2)], total_retrieved=1)

    monkeypatch.setattr(nodes, "rag_service", _FakeRAG())
    update = await nodes.critic_grader_node(_node_state())

    assert update["grader_verdict"] == VERDICT_INSUFFICIENT
    assert update["needs_web_search"] is True
    assert update["pending_tool_calls"][0]["name"] == "web_search"
    assert update["pending_tool_calls"][0]["arguments"]["query"] == "Tell me about RAG"


@pytest.mark.asyncio
async def test_critic_node_skips_web_search_on_relevant_evidence(monkeypatch):
    from backend.app.agents.orchestrator import nodes

    class _FakeRAG:
        async def query(self, **kwargs):
            return RAGQueryResult(query="q", citations=[_citation(0.7), _citation(0.6)], total_retrieved=2)

    monkeypatch.setattr(nodes, "rag_service", _FakeRAG())
    update = await nodes.critic_grader_node(_node_state())

    assert update["grader_verdict"] == VERDICT_RELEVANT
    assert update["needs_web_search"] is False
    assert update["pending_tool_calls"] == []
    assert len(update["citations"]) == 2


def test_route_after_critic_crag_edges():
    from backend.app.agents.orchestrator.graph import route_after_critic

    assert route_after_critic({"needs_web_search": True}) == "tool_node"
    assert route_after_critic({"needs_web_search": True, "pending_tool_calls": []}) == "tool_node"
    assert route_after_critic({"needs_web_search": False, "pending_tool_calls": []}) == "synthesizer"


@pytest.mark.asyncio
async def test_critic_node_falls_back_to_web_when_rag_errors(monkeypatch):
    """
    Regression: if the RAG index/vector store is down, the critic must NOT
    crash the whole turn — it defaults to an 'unrelated' verdict + web search.
    """
    from backend.app.agents.orchestrator import nodes

    class _BrokenRAG:
        async def query(self, **kwargs):
            raise RuntimeError("vector store unreachable")

    monkeypatch.setattr(nodes, "rag_service", _BrokenRAG())
    update = await nodes.critic_grader_node(_node_state())

    assert update["grader_verdict"] == VERDICT_UNRELATED
    assert update["needs_web_search"] is True
    assert update["citations"] == []
    assert update["pending_tool_calls"][0]["name"] == "web_search"


@pytest.mark.asyncio
async def test_synthesizer_handles_list_shaped_web_results(monkeypatch):
    """
    Regression: tool_gateway.execute_tool returns web-search results as a
    LIST of {title,url,snippet}; the synthesizer must format them without
    crashing (previously called .get() on the list → AttributeError).
    """
    from backend.app.agents.orchestrator import nodes

    async def fake_completion(messages, model, temperature):
        return "Answer referencing [1]."

    monkeypatch.setattr(nodes.ai_client, "completion", fake_completion)
    monkeypatch.setattr(nodes.long_term_memory, "add_from_conversation", async_noop)
    monkeypatch.setattr(nodes.long_term_memory, "build_memory_context_block", async_noop)

    state = _node_state(
        tool_results=[
            {
                "tool_name": "web_search",
                "status": "success",
                "result": [
                    {"title": "Alpha", "url": "https://alpha.example", "snippet": "alpha snippet", "content": "alpha"},
                    {"title": "Beta", "url": "https://beta.example", "snippet": "beta snippet", "content": "beta"},
                ],
            }
        ],
        subagent_outputs={},
        user_memories=[],
        citations=[],
        grader_verdict="insufficient",
    )
    state["user_id"] = str(uuid.uuid4())

    update = await nodes.synthesizer_node(state)
    assert update["messages"][0].content == "Answer referencing [1]."


async def async_noop(*args, **kwargs):
    return None
