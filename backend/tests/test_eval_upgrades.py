"""
Unit tests for the evaluation upgrades: arena judgment, multi-turn compliance,
and the prompt-regression golden gate (all offline/heuristic paths).
"""
import pytest

from backend.app.services.evaluation.deepeval_service import deepeval_service
from backend.app.services.evaluation.regression_service import GOLDEN_PROBES, PromptRegressionGate


@pytest.mark.asyncio
async def test_arena_judge_heuristic_fallback(monkeypatch):
    """When the LLM judge is unavailable, the groundedness/informativeness
    heuristic must still produce a verdict within [0, 1]."""

    class _FakeArena:
        def __init__(self, **kwargs):
            pass

        async def a_measure(self, test_case):
            raise RuntimeError("no api key")

    monkeypatch.setattr("deepeval.metrics.ArenaGEval", _FakeArena)

    result = await deepeval_service.evaluate_arena_pair(
        query="What is the capital of France?",
        output_a="Paris is the capital of France.",  # grounded & informative
        output_b="I think it might be somewhere in Europe.",  # vague
    )
    assert result.metric == "arena_judge"
    assert 0.0 <= result.score <= 1.0
    assert "Heuristic" in result.reason


@pytest.mark.asyncio
async def test_arena_judge_prefers_grounded_output(monkeypatch):
    class _FakeArena:
        def __init__(self, **kwargs):
            pass

        async def a_measure(self, test_case):
            raise RuntimeError("offline")

    monkeypatch.setattr("deepeval.metrics.ArenaGEval", _FakeArena)

    grounded = await deepeval_service.evaluate_arena_pair(
        query="2+2",
        output_a="The answer is 4.",
        output_b="Hmm.",
    )
    assert grounded.score > 0.5  # A beats B (grounded + far more informative)


@pytest.mark.asyncio
async def test_conversational_compliance_heuristic(monkeypatch):
    class _FakeConv:
        def __init__(self, **kwargs):
            pass

        async def a_measure(self, test_case):
            raise RuntimeError("offline")

    monkeypatch.setattr("deepeval.metrics.ConversationalGEval", _FakeConv)

    transcript = [
        {"input": "Should I buy Tesla stock?", "actual_output": "I can't give personalized investment advice; consider a professional."},
        {"input": "What is 2+2?", "actual_output": "4."},
    ]
    results = await deepeval_service.evaluate_conversational(transcript)
    assert results[0].metric == "conversational_compliance"
    assert results[0].score >= 0.7


@pytest.mark.asyncio
async def test_conversational_empty_transcript():
    results = await deepeval_service.evaluate_conversational([])
    assert results[0].passed is False


def test_offline_judge_passes_critical_probe():
    outcome = PromptRegressionGate._offline_judge(GOLDEN_PROBES[0])
    assert outcome["passed"] is True
    assert outcome["judge"] == "offline"
    assert outcome["critical"] is True


@pytest.mark.asyncio
async def test_prompt_regression_gate_offline_fallback(monkeypatch):
    class _FailingClient:
        async def completion(self, messages, **kwargs):
            raise RuntimeError("no credentials")

    monkeypatch.setattr("backend.app.services.evaluation.regression_service.ai_client", _FailingClient())

    report = await PromptRegressionGate().evaluate_prompt(system_prompt="You are a safe assistant.")
    assert report["gate"] in ("passed", "failed")
    assert report["probes_total"] == len(GOLDEN_PROBES)
    assert 0.0 <= report["score"] <= 1.0
    for res in report["results"]:
        assert "judge" in res


@pytest.mark.asyncio
async def test_prompt_regression_gate_fails_when_probe_unanswerable(monkeypatch):
    """A probe with no pass markers and no LLM judge must register a failure."""
    class _FailingClient:
        async def completion(self, messages, **kwargs):
            raise RuntimeError("no credentials")

    monkeypatch.setattr("backend.app.services.evaluation.regression_service.ai_client", _FailingClient())

    gate = PromptRegressionGate()
    probes = [{"id": "no-marker", "user_query": "x", "required_behavior": "y", "pass_markers": [], "fail_markers": [], "critical": True}]
    report = await gate.evaluate_prompt(system_prompt="t", probes=probes)
    assert report["probes_passed"] == 0
    assert report["gate"] == "failed"
    assert len(report["critical_failures"]) == 1