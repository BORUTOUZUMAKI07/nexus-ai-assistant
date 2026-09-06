"""
Fault-injection resilience tests for degraded-mode behavior.

Each test simulates a failing dependency (provider, sandbox, retrieval,
structured-output, guardrail, memory, encoder) and asserts the surrounding
service degrades gracefully via its documented fallback/retry seam instead
of propagating the failure.
"""
import builtins
from types import SimpleNamespace

import litellm
import pytest
from backend.app.agents.orchestrator import nodes
from backend.app.agents.subagents import coder as coder_module
from backend.app.agents.subagents.coder import coder_subagent
from backend.app.infrastructure.ai.litellm_client import litellm_service
from backend.app.services.evaluation.deepeval_service import deepeval_service
from backend.app.services.evaluation.guardrail_service import guardrail_service
from backend.app.services.tools.web_search import WebSearchService
from ddgs.exceptions import DDGSException
from langchain_core.messages import HumanMessage

# ── WebSearchService priority ladder ──────────────────────────────────────────


async def _search_tavily_fails(*args, **kwargs):
    raise RuntimeError("tavily down")


async def _search_tavily_empty(*args, **kwargs):
    return []


async def _search_firecrawl_ok(*args, **kwargs):
    return [{"title": "FC", "url": "https://fc", "snippet": "s", "content": "c"}]


async def _search_duckduckgo_ok(*args, **kwargs):
    return [{"title": "DDG", "url": "https://ddg", "snippet": "s", "content": ""}]


async def _search_duckduckgo_fails(*args, **kwargs):
    raise RuntimeError("ddg down")


@pytest.mark.asyncio
async def test_web_search_tavily_failure_falls_back_to_firecrawl(monkeypatch):
    service = WebSearchService(
        tavily_key="tv-key",
        firecrawl_key="fc-valid-key",
    )
    monkeypatch.setattr(service, "_search_tavily", _search_tavily_fails)
    monkeypatch.setattr(service, "_search_firecrawl", _search_firecrawl_ok)
    monkeypatch.setattr(service, "_search_duckduckgo", _search_duckduckgo_fails)

    results = await service.search("query")

    assert results == [{"title": "FC", "url": "https://fc", "snippet": "s", "content": "c"}]


@pytest.mark.asyncio
async def test_web_search_empty_tavily_results_falls_through_to_firecrawl(monkeypatch):
    service = WebSearchService(tavily_key="tv-key", firecrawl_key="fc-valid-key")
    monkeypatch.setattr(service, "_search_tavily", _search_tavily_empty)
    monkeypatch.setattr(service, "_search_firecrawl", _search_firecrawl_ok)

    results = await service.search("query")

    assert results and results[0]["title"] == "FC"


@pytest.mark.asyncio
async def test_web_search_placeholder_firecrawl_key_skips_firecrawl(monkeypatch):
    service = WebSearchService(
        tavily_key="tv-key",
        firecrawl_key="fc_placeholder_do_not_use",
    )
    monkeypatch.setattr(service, "_search_tavily", _search_tavily_fails)
    monkeypatch.setattr(service, "_search_duckduckgo", _search_duckduckgo_ok)

    results = await service.search("query")

    assert results and results[0]["title"] == "DDG"


@pytest.mark.asyncio
async def test_web_search_all_providers_fail_returns_empty(monkeypatch):
    service = WebSearchService(tavily_key="tv-key", firecrawl_key="fc-valid-key")
    monkeypatch.setattr(service, "_search_tavily", _search_tavily_fails)
    monkeypatch.setattr(service, "_search_duckduckgo", _search_duckduckgo_fails)

    async def _search_firecrawl_empty(*args, **kwargs):
        return []

    monkeypatch.setattr(service, "_search_firecrawl", _search_firecrawl_empty)

    results = await service.search("query")

    assert results == []


@pytest.mark.asyncio
async def test_web_search_duckduckgo_retries_with_backoff(monkeypatch):
    flaky = SimpleNamespace()

    class FlakyDDGS:
        def __init__(self):
            flaky.calls = 0

        def text(self, query, max_results, backend, safesearch):
            flaky.calls += 1
            if flaky.calls <= 2:
                raise DDGSException("blocked")
            return [{"title": "ok", "href": "https://ok", "body": "body"}]

    monkeypatch.setattr("ddgs.DDGS", FlakyDDGS)

    service = WebSearchService(tavily_key="", firecrawl_key="")
    results = await service.search("query")

    assert flaky.calls == 3
    assert len(results) == 1
    assert results[0]["title"] == "ok"


# ── DeepEval heuristic fallback ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deepeval_falls_back_to_heuristic_when_native_evaluation_missing(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "deepeval.metrics":
            raise ImportError("deepeval not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    results = await deepeval_service.evaluate_rag_turn(
        query="q",
        actual_output="This is a sufficiently long synthesized answer for grading.",
        retrieval_context=["grounding evidence from the retrieved document"],
    )

    assert len(results) == 2
    assert [r.metric for r in results] == ["faithfulness", "answer_relevancy"]
    assert all(r.reason for r in results)
    assert all(0.0 <= r.score <= 1.0 for r in results)


# ── CoderSubagent self-correction loop ────────────────────────────────────────


@pytest.mark.asyncio
async def test_coder_subagent_succeeds_on_first_attempt(monkeypatch):
    async def _completion(*args, **kwargs):
        return "```python\nprint('hi')\n```"

    async def _execution_success(code, *args, **kwargs):
        return {"success": True, "stdout": "hi", "charts": []}

    monkeypatch.setattr(coder_module, "ai_client", SimpleNamespace(completion=_completion))
    monkeypatch.setattr(coder_module, "code_executor", SimpleNamespace(execute_python=_execution_success))

    res = await coder_subagent.execute(task_description="print hi")

    assert res["status"] == "success"
    assert res["attempts"] == 1
    assert "print('hi')" in res["code"]


@pytest.mark.asyncio
async def test_coder_subagent_retries_then_succeeds(monkeypatch):
    async def _completion(*args, **kwargs):
        return "```python\nprint('hi')\n```"

    attempts = {"n": 0}

    async def _execution_flaky(code, *args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return {"success": False, "error": "syntax error"}
        return {"success": True, "stdout": "hi", "charts": []}

    monkeypatch.setattr(coder_module, "ai_client", SimpleNamespace(completion=_completion))
    monkeypatch.setattr(coder_module, "code_executor", SimpleNamespace(execute_python=_execution_flaky))

    res = await coder_subagent.execute(task_description="print hi", max_retries=2)

    assert res["status"] == "success"
    assert res["attempts"] == 3


@pytest.mark.asyncio
async def test_coder_subagent_fails_after_exhausting_retries(monkeypatch):
    async def _completion(*args, **kwargs):
        return "```python\nprint('hi')\n```"

    async def _execution_fail(code, *args, **kwargs):
        return {"success": False, "error": "persistent bug"}

    monkeypatch.setattr(coder_module, "ai_client", SimpleNamespace(completion=_completion))
    monkeypatch.setattr(coder_module, "code_executor", SimpleNamespace(execute_python=_execution_fail))

    res = await coder_subagent.execute(task_description="print hi", max_retries=2)

    assert res["status"] == "failed"
    assert res["attempts"] == 3
    assert "persistent bug" in res["error"]


# ── Orchestrator routing: structured-output → raw fallback ────────────────────


async def _structured_raises(*args, **kwargs):
    raise RuntimeError("instructor parse failed")


async def _completion_research(*args, **kwargs):
    return "RESEARCH"


@pytest.mark.asyncio
async def test_orchestrator_routes_via_raw_fallback_when_structured_output_fails(monkeypatch):
    monkeypatch.setattr(nodes.structured_service, "generate_structured", _structured_raises)
    monkeypatch.setattr(nodes.ai_client, "completion", _completion_research)

    state = {"messages": [HumanMessage(content="find current news")], "mode": "normal"}

    update = await nodes.orchestrator_node(state)

    assert update["subagent_dispatches"] == ["researcher"]
    assert update["task_type"] == "research"


@pytest.mark.asyncio
async def test_orchestrator_uses_structured_routing_when_available(monkeypatch):
    async def _structured_answer(response_model=None, **kwargs):
        return SimpleNamespace(action="ANSWER")

    monkeypatch.setattr(nodes.structured_service, "generate_structured", _structured_answer)

    state = {"messages": [HumanMessage(content="what is 2+2")], "mode": "normal"}

    update = await nodes.orchestrator_node(state)

    assert update["subagent_dispatches"] == []
    assert update["task_type"] == "general"


# ── Critic/grader: RAG degradation → CRAG web-search supplement ───────────────


async def _rag_query_fails(*args, **kwargs):
    raise RuntimeError("qdrant/embedding infrastructure unavailable")


@pytest.mark.asyncio
async def test_critic_grader_falls_back_to_web_when_rag_unavailable(monkeypatch):
    monkeypatch.setattr(nodes.rag_service, "query", _rag_query_fails)

    state = {"messages": [HumanMessage(content="tell me about hybrid search")], "user_id": "user-1"}

    update = await nodes.critic_grader_node(state)

    assert update["grader_verdict"] == "unrelated"
    assert update["needs_web_search"] is True
    assert update["citations"] == []
    assert update["pending_tool_calls"] == [
        {"name": "web_search", "arguments": {"query": "tell me about hybrid search", "max_results": 5}}
    ]


# ── Synthesizer: best-effort blocks degrade without failing the turn ──────────


async def _completion_draft(*args, **kwargs):
    return "The final synthesized answer to show the user."


async def _critic_approves(*args, **kwargs):
    return {"approved": True, "critique": ""}


def _guardrail_raises(*args, **kwargs):
    raise RuntimeError("guardrail model unavailable")


def _evidence_gate_raises(*args, **kwargs):
    raise RuntimeError("evidence gate error")


async def _memory_save_raises(*args, **kwargs):
    raise RuntimeError("mem0 unavailable")


@pytest.mark.asyncio
async def test_synthesizer_survives_guardrail_evidence_and_memory_failures(monkeypatch):
    monkeypatch.setattr(nodes.ai_client, "completion", _completion_draft)
    monkeypatch.setattr(nodes.critic_subagent, "evaluate", _critic_approves)
    monkeypatch.setattr(guardrail_service, "redact_pii", _guardrail_raises)
    monkeypatch.setattr(nodes.evidence_gate, "verify_evidence_support", _evidence_gate_raises)
    monkeypatch.setattr(nodes.long_term_memory, "add_from_conversation", _memory_save_raises)

    state = {
        "messages": [HumanMessage(content="answer this question")],
        "system_prompt": "You are Nexus AI.",
        "user_id": "user-1",
    }

    update = await nodes.synthesizer_node(state)

    assert len(update["messages"]) == 1
    assert update["messages"][0].content == "The final synthesized answer to show the user."
    assert update["evidence_gate_passed"] is False
    assert update["revision_count"] == 0


# ── LiteLLM token-counting fallback ───────────────────────────────────────────


def _encode_raises(*args, **kwargs):
    raise RuntimeError("tokenizer unavailable")


def test_litellm_count_tokens_falls_back_to_word_count(monkeypatch):
    monkeypatch.setattr(litellm, "encode", _encode_raises)

    assert litellm_service.count_tokens("hello world") == 2
