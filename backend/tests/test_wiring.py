"""
Tests for wiring of the previously-dead service modules:
  - Cost tracking (calculate_cost, monthly CostLog rollup)
  - Evidence Gate (factual confidence threshold)
  - Quality metrics
  - Structured-output routing with raw fallback (orchestrator node)
  - Critic subagent self-refinement loop in the synthesizer node
  - CAG prompt caching in the prompt compiler
  - Helicone headers
  - Celery file-indexing task dispatch config
  - Admin evaluation router handlers
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from backend.app.core.config import settings
from backend.app.domain.usage.models import CostLog
from backend.app.services.evaluation.quality_service import quality_service
from backend.app.services.observability.cost_tracking import (
    cost_tracking_service,
)
from backend.app.services.observability.helicone import helicone_service
from backend.app.services.prompt_compiler import prompt_compiler
from backend.app.services.tools.evidence_gate import EVIDENCE_THRESHOLD, evidence_gate


async def async_noop(*args, **kwargs):
    return None


# ─── 1. Cost Tracking ────────────────────────────────────────────────────────


def test_cost_calc_strips_provider_prefix_and_matches_pricing():
    assert cost_tracking_service.calculate_cost("groq/llama-3.3-70b-versatile", 1_000_000, 0) == 0.59
    assert cost_tracking_service.calculate_cost("llama-3.1-8b-instant", 0, 1_000_000) == 0.08
    assert cost_tracking_service.calculate_cost("unknown/model", 1_000_000, 0) == 0.50  # default table


@pytest.mark.asyncio
async def test_record_cost_log_persists_aggregate_cost_entry(monkeypatch):
    created: list = []
    fake_repo = MagicMock()

    async def fake_create(entity):
        created.append(entity)
        return entity

    fake_repo.create = fake_create
    monkeypatch.setattr("backend.app.services.observability.cost_tracking.UsageRepository", lambda _s: fake_repo)

    result = await cost_tracking_service.record_cost_log(
        session=MagicMock(),
        user_id=uuid.uuid4(),
        model="groq/llama-3.3-70b-versatile",
        provider="groq",
        prompt_tokens=1_000_000,
        completion_tokens=0,
    )

    assert isinstance(result, CostLog)
    assert result.total_cost == 0.59
    assert result.input_cost == 0.59
    assert result.output_cost == 0.0
    assert len(result.billing_period) == 7  # YYYY-MM


# ─── 2. Evidence Gate ────────────────────────────────────────────────────────


def test_evidence_gate_fails_on_empty_context():
    verdict = evidence_gate.verify_evidence_support("Some answer.", [])
    assert verdict["passed_gate"] is False
    assert verdict["confidence_score"] == 0.0


def test_evidence_gate_passes_with_cited_context():
    verdict = evidence_gate.verify_evidence_support(
        "The answer is A. See [1] for details.",
        ["Context sentence supporting the answer."],
    )
    assert verdict["passed_gate"] is True
    assert verdict["confidence_score"] >= EVIDENCE_THRESHOLD
    assert len(verdict["citations"]) == 1


def test_evidence_gate_uncited_context_fails():
    verdict = evidence_gate.verify_evidence_support(
        "An assertion with no markers.",
        ["Context sentence that exists."],
    )
    assert verdict["passed_gate"] is False


# ─── 3. Quality Metrics ──────────────────────────────────────────────────────


def test_quality_service_marks_empty_response_invalid():
    metrics = quality_service.evaluate_response_quality("p", "", 0, 0)
    assert metrics["is_valid"] is False


def test_quality_service_computes_tokens_per_second():
    metrics = quality_service.evaluate_response_quality("p", "Hello world", 4, 1000)
    assert metrics["tokens_per_second"] == 4.0
    assert metrics["word_count"] == 2
    assert metrics["is_valid"] is True


# ─── 4. Structured Router (orchestrator node) ─────────────────────────────────


def _router_state(**overrides) -> dict:
    state = {"user_id": str(uuid.uuid4()), "mode": "normal", "messages": [MagicMock()]}
    state["messages"][0].content = "Write a sorting function"
    state.update(overrides)
    return state


@pytest.mark.asyncio
async def test_orchestrator_structured_route_uses_structured_service(monkeypatch):
    from backend.app.agents.orchestrator import nodes

    async def fake_structured(**kwargs):
        return MagicMock(action="CODE")

    monkeypatch.setattr(nodes.structured_service, "generate_structured", fake_structured)
    update = await nodes.orchestrator_node(_router_state())
    assert update["subagent_dispatches"] == ["coder"]
    assert update["task_type"] == "code"


@pytest.mark.asyncio
async def test_orchestrator_falls_back_to_raw_completion_when_structured_fails(monkeypatch):
    from backend.app.agents.orchestrator import nodes

    async def broken_structured(**kwargs):
        raise RuntimeError("instructor unavailable")

    async def fake_completion(messages, model, temperature):
        return "RESEARCH"

    monkeypatch.setattr(nodes.structured_service, "generate_structured", broken_structured)
    monkeypatch.setattr(nodes.ai_client, "completion", fake_completion)

    update = await nodes.orchestrator_node(_router_state())
    assert update["subagent_dispatches"] == ["researcher"]
    assert update["task_type"] == "research"


# ─── 5. Critic Self-Refinement (synthesizer node) ─────────────────────────────


@pytest.mark.asyncio
async def test_synthesizer_revises_draft_until_critic_approves(monkeypatch):
    from backend.app.agents.orchestrator import nodes

    calls = {"completion": 0, "critic": 0}

    async def fake_completion(messages, model, temperature):
        calls["completion"] += 1
        return "final draft answer"

    async def fake_critic(user_request, candidate_response):
        calls["critic"] += 1
        if calls["critic"] == 1:
            return {"subagent": "critic", "approved": False, "critique": "Add more detail and cite sources."}
        return {"subagent": "critic", "approved": True, "critique": "Approved."}

    monkeypatch.setattr(nodes.ai_client, "completion", fake_completion)
    monkeypatch.setattr(nodes.critic_subagent, "evaluate", fake_critic)
    monkeypatch.setattr(nodes.long_term_memory, "add_from_conversation", async_noop)

    state = {
        "user_id": str(uuid.uuid4()),
        "conversation_id": str(uuid.uuid4()),
        "system_prompt": "You are Nexus AI.",
        "subagent_outputs": {},
        "tool_results": [],
        "user_memories": [],
        "citations": [],
        "grader_verdict": "insufficient",
        "revision_count": 0,
        "messages": [MagicMock()],
    }
    state["messages"][0].content = "Summarize the RAG research"

    update = await nodes.synthesizer_node(state)

    assert calls["critic"] == 2  # one rejection, then approval
    assert calls["completion"] == 2
    assert update["messages"][0].content == "final draft answer"
    assert update["revision_count"] == 1
    assert update["critique"]["approved"] is True
    assert update["evidence_gate_passed"] is False  # no citations provided


@pytest.mark.asyncio
async def test_synthesizer_bounds_revisions_when_critic_keeps_rejecting(monkeypatch):
    from backend.app.agents.orchestrator import nodes

    async def fake_completion(messages, model, temperature):
        return "draft"

    async def always_reject(user_request, candidate_response):
        return {"subagent": "critic", "approved": False, "critique": "never good enough"}

    monkeypatch.setattr(nodes.ai_client, "completion", fake_completion)
    monkeypatch.setattr(nodes.critic_subagent, "evaluate", always_reject)
    monkeypatch.setattr(nodes.long_term_memory, "add_from_conversation", async_noop)

    state = {
        "user_id": str(uuid.uuid4()),
        "system_prompt": "You are Nexus AI.",
        "subagent_outputs": {},
        "tool_results": [],
        "user_memories": [],
        "citations": [],
        "grader_verdict": "unrelated",
        "revision_count": 0,
        "messages": [MagicMock()],
    }
    state["messages"][0].content = "Do a thing"

    update = await nodes.synthesizer_node(state)

    max_revisions = int(getattr(settings, "CRITIC_MAX_REVISIONS", 2))
    assert update["revision_count"] == max_revisions  # budget fully consumed
    assert update["messages"][0].content == "draft"  # accepted as-is


# ─── 6. CAG Prompt Caching ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prompt_compiler_cag_cache_hit(monkeypatch):
    from backend.app.services import prompt_compiler as pc_module

    monkeypatch.setattr(
        pc_module,
        "cag_service",
        MagicMock(get_static_context=AsyncMock(return_value="CACHED PROMPT 123")),
    )
    result = await prompt_compiler.compile_system_prompt_cached(
        custom_instructions="Be helpful", active_skills=None
    )
    assert result == "CACHED PROMPT 123"


@pytest.mark.asyncio
async def test_prompt_compiler_cag_miss_compiles_and_falls_back_when_redis_down(monkeypatch):
    from backend.app.services import prompt_compiler as pc_module

    written = {}

    async def raise_get(_key):
        raise ConnectionError("redis offline")

    async def fake_set(key, content, ttl_seconds=None):
        written[key] = content
        return True

    monkeypatch.setattr(pc_module, "cag_service", MagicMock(
        get_static_context=raise_get,
        set_static_context=fake_set,
    ))

    result = await prompt_compiler.compile_system_prompt_cached(
        custom_instructions="Be concise"
    )
    assert isinstance(result, str)
    assert "Be concise" in result
    assert written  # compiled prompt cached for the next run


@pytest.mark.asyncio
async def test_prompt_compiler_cag_skips_caching_for_dynamic_inputs(monkeypatch):
    from backend.app.services import prompt_compiler as pc_module

    async def raise_get(_key):
        raise AssertionError("should not even read cache for dynamic input")

    never_set = MagicMock(side_effect=AssertionError("should not call set for dynamic input"))

    monkeypatch.setattr(pc_module, "cag_service", MagicMock(
        get_static_context=raise_get,
        set_static_context=never_set,
    ))

    result = await prompt_compiler.compile_system_prompt_cached(
        custom_instructions=None, user_memories=[MagicMock()], retrieved_context="[1] File data"
    )
    assert isinstance(result, str)


# ─── 7. Helicone Headers ──────────────────────────────────────────────────────


def test_helicone_headers_noop_without_api_key():
    original = settings.HELICONE_API_KEY
    try:
        settings.HELICONE_API_KEY = None
        assert helicone_service.get_headers(user_id="u1", conversation_id="c1") == {}
    finally:
        settings.HELICONE_API_KEY = original


def test_helicone_headers_inject_session_and_user_when_configured():
    original = settings.HELICONE_API_KEY
    try:
        settings.HELICONE_API_KEY = "h-k"
        headers = helicone_service.get_headers(user_id="u9", conversation_id="c9", properties={"env": "dev"})
        assert headers["Helicone-Auth"] == "Bearer h-k"
        assert headers["Helicone-User-Id"] == "u9"
        assert headers["Helicone-Session-Id"] == "c9"
        assert headers["Helicone-Property-env"] == "dev"
    finally:
        settings.HELICONE_API_KEY = original


# ─── 8. Celery File-Indexing Dispatch Config ──────────────────────────────────


def test_async_indexing_flag_defaults_off():
    assert settings.ASYNC_INDEXING is False


def test_process_file_indexing_task_resolves():
    from backend.app.worker.tasks import process_file_indexing_task

    assert process_file_indexing_task.name == "tasks.process_file_indexing"


# ─── 9. Admin Evaluation Router Handlers ──────────────────────────────────────


@pytest.mark.asyncio
async def test_quality_eval_handler_returns_metrics():
    from backend.app.api.v1.evaluation import QualityRequest, run_quality_eval

    resp = await run_quality_eval(
        body=QualityRequest(prompt="q", response="Some good answer here.", tokens_used=10, latency_ms=500.0),
        admin=MagicMock(),
    )
    assert resp["evaluator"] == "quality"
    assert resp["metrics"]["is_valid"] is True


def test_evaluation_router_registers_admin_scoped_routes():
    from backend.app.api.v1 import evaluation

    paths = {r.path for r in evaluation.router.routes}
    assert "/admin/evaluation/rag" in paths
    assert "/admin/evaluation/deepeval" in paths
    assert "/admin/evaluation/redteam" in paths
    assert "/admin/evaluation/quality" in paths
