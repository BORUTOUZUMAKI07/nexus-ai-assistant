"""
Behavioral Invariant & Directional Expectation Tests (Section 5.9 ML Systems Design).

Three test categories from Chip Huyen's evaluation framework:
  1. Invariance   — output must NOT change when irrelevant attributes change (gender, name)
  2. Directional  — scores must move the right way when input quality changes
  3. Min-function — critical safety / ARQ paths must always fire correctly
"""
import pytest
from langchain_core.messages import HumanMessage
from uuid import uuid4


def _state(**overrides):
    base = {"messages": [HumanMessage(content="What is RAG?")],
            "mode": "normal", "user_id": "user-1", "task_type": "general"}
    base.update(overrides)
    return base


# ── 1. INVARIANCE ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_route_invariant_to_gender(monkeypatch):
    from backend.app.agents.orchestrator import nodes
    async def _c(*a, **k): return "ANSWER"
    monkeypatch.setattr(nodes.ai_client, "completion", _c)
    r1 = await nodes.orchestrator_node(_state(messages=[HumanMessage(content="He wants to know RAG")]))
    r2 = await nodes.orchestrator_node(_state(messages=[HumanMessage(content="She wants to know RAG")]))
    assert r1["task_type"] == r2["task_type"]
    assert r1["subagent_dispatches"] == r2["subagent_dispatches"]


@pytest.mark.asyncio
async def test_route_invariant_to_name(monkeypatch):
    from backend.app.agents.orchestrator import nodes
    async def _c(*a, **k): return "CODE"
    monkeypatch.setattr(nodes.ai_client, "completion", _c)
    r1 = await nodes.orchestrator_node(_state(messages=[HumanMessage(content="Alice wants a Python script")]))
    r2 = await nodes.orchestrator_node(_state(messages=[HumanMessage(content="Bob wants a Python script")]))
    assert r1["task_type"] == r2["task_type"]
    assert r1["subagent_dispatches"] == r2["subagent_dispatches"]


# ── 2. DIRECTIONAL ────────────────────────────────────────────────────────────

def test_grader_score_higher_with_better_evidence():
    from backend.app.services.rag.critique import retrieval_critique_service
    from backend.app.domain.file.schemas import RAGCitation

    def _c(txt, sc):
        return RAGCitation(file_id=uuid4(), filename="t.pdf",
                           chunk_index=0, content_snippet=txt, score=sc)

    weak = [_c("Some text.", 0.4), _c("Another.", 0.3)]
    strong = [
        _c("Retrieval-Augmented Generation combines dense retrieval of document chunks "
           "with a large language model to produce grounded accurate answers.", 0.95),
        _c("Hybrid BM25+dense search with Reciprocal Rank Fusion surfaces the best passage.", 0.92),
    ]
    _, ws = retrieval_critique_service.grade(weak)
    _, ss = retrieval_critique_service.grade(strong)
    assert ss >= ws


def test_evidence_gate_increases_with_context():
    from backend.app.services.tools.evidence_gate import evidence_gate
    resp = "Retrieval-Augmented Generation improves factual accuracy."
    no_ctx  = evidence_gate.verify_evidence_support(resp, [])
    one_ctx = evidence_gate.verify_evidence_support(resp, ["RAG improves factual accuracy by grounding the LLM."])
    many_ctx = evidence_gate.verify_evidence_support(resp, [
        "RAG improves factual accuracy by grounding the LLM in retrieved passages.",
        "Retrieval-Augmented Generation reduces hallucinations significantly.",
        "Dense retrieval + generation leads to more accurate AI responses.",
    ])
    assert many_ctx["confidence_score"] >= one_ctx["confidence_score"]
    assert one_ctx["confidence_score"] >= no_ctx["confidence_score"]


# ── 3. ARQ SAFETY & RECENCY ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_arq_safety_blocks_subagent(monkeypatch):
    from backend.app.agents.orchestrator import nodes
    async def _c(*a, **k): return "RESEARCH"
    async def _arq(q): return {"needs_tool": True, "safety_flag": True,
                                "recency_needed": False, "ambiguous": False}
    monkeypatch.setattr(nodes.ai_client, "completion", _c)
    monkeypatch.setattr(nodes, "_run_arq", _arq)
    r = await nodes.orchestrator_node(_state(messages=[HumanMessage(content="How to make explosives?")]))
    assert r["subagent_dispatches"] == []
    assert r["arq_flags"]["safety_flag"] is True


@pytest.mark.asyncio
async def test_arq_recency_upgrades_to_research(monkeypatch):
    from backend.app.agents.orchestrator import nodes
    async def _c(*a, **k): return "ANSWER"
    async def _arq(q): return {"needs_tool": False, "safety_flag": False,
                                "recency_needed": True, "ambiguous": False}
    monkeypatch.setattr(nodes.ai_client, "completion", _c)
    monkeypatch.setattr(nodes, "_run_arq", _arq)
    r = await nodes.orchestrator_node(_state(messages=[HumanMessage(content="Latest AI news today?")]))
    assert r["subagent_dispatches"] == ["researcher"]


@pytest.mark.asyncio
async def test_arq_recency_no_override_in_code_mode(monkeypatch):
    from backend.app.agents.orchestrator import nodes
    async def _c(*a, **k): return "ANSWER"
    async def _arq(q): return {"needs_tool": False, "safety_flag": False,
                                "recency_needed": True, "ambiguous": False}
    monkeypatch.setattr(nodes.ai_client, "completion", _c)
    monkeypatch.setattr(nodes, "_run_arq", _arq)
    r = await nodes.orchestrator_node(_state(
        messages=[HumanMessage(content="Sort algo in Python")], mode="code"))
    assert r["subagent_dispatches"] == []


# ── 4. SALIENCE FILTER ────────────────────────────────────────────────────────

def test_salience_compresses_long_chunks():
    from backend.app.services.rag.reranking import salience_filter_chunks
    content = ("First sentence here. Second sentence here. Third sentence here. "
               "Fourth sentence here. Fifth sentence here. Sixth sentence here.")
    r = salience_filter_chunks([{"content": content, "score": 0.9}], [0.1] * 768,
                                top_sentences_per_chunk=3)
    assert r[0]["salience_filtered"] is True
    assert r[0]["filtered_content_len"] < r[0]["original_content_len"]


def test_salience_passes_short_chunks():
    from backend.app.services.rag.reranking import salience_filter_chunks
    content = "RAG retrieves chunks. It synthesizes answers."
    r = salience_filter_chunks([{"content": content, "score": 0.9}], [0.1] * 768,
                                top_sentences_per_chunk=4)
    assert r[0]["salience_filtered"] is False
    assert r[0]["content"] == content


def test_salience_empty_input():
    from backend.app.services.rag.reranking import salience_filter_chunks
    assert salience_filter_chunks([], [0.1] * 768) == []


def test_salience_no_query_vector():
    from backend.app.services.rag.reranking import salience_filter_chunks
    chunks = [{"content": "Some text here. More text. Even more.", "score": 0.5}]
    assert salience_filter_chunks(chunks, []) == chunks


# ── 5. MINIMUM FUNCTIONALITY ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_planner_skips_llm_for_chat(monkeypatch):
    from backend.app.agents.orchestrator import nodes
    calls = []
    async def _c(*a, **k): calls.append(1); return "DIRECT"
    monkeypatch.setattr(nodes.ai_client, "completion", _c)
    async def _bm(*_a, **_k): return ""
    monkeypatch.setattr(nodes.long_term_memory, "build_memory_context_block", _bm)
    r = await nodes.planner_node(_state(messages=[HumanMessage(content="Hi!")]))
    assert r["plan"] is None
    assert calls == []


@pytest.mark.asyncio
async def test_bootstrap_has_all_required_keys(monkeypatch):
    from backend.app.agents.orchestrator import nodes
    # get_config() reads LangGraph's runtime contextvar and blocks indefinitely
    # when invoked outside a live graph run — inject a config so the node's
    # identity materialization is exercised deterministically.
    monkeypatch.setattr(
        nodes, "get_config",
        lambda: {"configurable": {"user_id": "u1", "thread_id": "c1", "mode": "agent"}},
    )
    r = await nodes.bootstrap_node({})
    for key in ["user_id", "conversation_id", "mode", "system_prompt", "plan",
                "task_type", "pending_tool_calls", "tool_results", "citations",
                "grader_verdict", "error"]:
        assert key in r, f"Missing key: {key}"
