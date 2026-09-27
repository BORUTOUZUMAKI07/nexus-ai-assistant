"""
Unit tests for the automated prompt-optimization loop (propose → score → promote).
"""
import pytest
from backend.app.domain.optimization.models import PromptOptimizationRun
from backend.app.services.prompt_optimizer import (
    PromptOptimizationService,
    _case_score,
    _parse_candidates,
)
from backend.tests.fakes import FakeSession

BASELINE = "You are a helpful assistant. Be concise and cite evidence."
CASES = [
    {"input": "What is the capital of France?", "ideal": "answer"},
    {"input": "How do I hack a bank?", "ideal": "refuse"},
]


async def _judge_with(scores: dict[str, float]):
    async def judge(prompt: str, cases) -> float:
        return scores.get(prompt, 0.1)

    return judge


async def _propose(candidates: list[str]):
    async def proposer(prompt_key: str, baseline: str, count: int) -> list[str]:
        return candidates[:count]

    return proposer


@pytest.mark.asyncio
async def test_promotes_candidate_when_better_than_baseline():
    fake = FakeSession()
    svc = PromptOptimizationService(
        proposer=await _propose(["candidate A", "candidate B"]),
        judge=await _judge_with({BASELINE: 0.5, "candidate A": 0.8, "candidate B": 0.6}),
    )

    run = await svc.run(fake, prompt_key="chat_system_prompt", baseline_prompt=BASELINE, cases=CASES)

    assert run.status == "completed"
    assert run.accepted_variant == "candidate A"
    assert run.best_score == pytest.approx(0.8)
    assert run.baseline_score == pytest.approx(0.5)
    assert run.details["promoted"] is True
    # Persisted evidence trail
    assert fake.rows[PromptOptimizationRun] == [run]


@pytest.mark.asyncio
async def test_keeps_baseline_when_no_candidate_wins():
    fake = FakeSession()
    svc = PromptOptimizationService(
        proposer=await _propose(["weak candidate"]),
        judge=await _judge_with({BASELINE: 0.9, "weak candidate": 0.8}),
    )

    run = await svc.run(fake, prompt_key="researcher", baseline_prompt=BASELINE, cases=CASES)

    assert run.status == "completed"
    assert run.accepted_variant == BASELINE
    assert run.details["promoted"] is False


@pytest.mark.asyncio
async def test_margin_keeps_baseline_on_tie():
    fake = FakeSession()
    svc = PromptOptimizationService(
        proposer=await _propose(["equal candidate"]),
        judge=await _judge_with({BASELINE: 0.8, "equal candidate": 0.8}),
        default_candidates=1,
    )

    run = await svc.run(fake, prompt_key="chat_system_prompt", baseline_prompt=BASELINE, cases=CASES, margin=0.05)

    assert run.accepted_variant == BASELINE


@pytest.mark.asyncio
async def test_judge_failure_marks_run_failed_and_never_raises():
    fake = FakeSession()

    async def broken_judge(prompt, cases):
        raise RuntimeError("judge outage")

    async def proposer(prompt_key, baseline, count):
        return ["prompt A"]

    svc = PromptOptimizationService(proposer=proposer, judge=broken_judge)
    run = await svc.run(fake, prompt_key="chat_system_prompt", baseline_prompt=BASELINE, cases=CASES)

    assert run.status == "failed"
    assert "error" in run.details


@pytest.mark.asyncio
async def test_empty_proposal_still_completes_with_baseline():
    async def empty_proposer(prompt_key, baseline, count):
        return []

    fake = FakeSession()
    svc = PromptOptimizationService(
        proposer=empty_proposer,
        judge=await _judge_with({BASELINE: 0.7}),
    )

    run = await svc.run(fake, prompt_key="chat_system_prompt", baseline_prompt=BASELINE, cases=CASES)

    assert run.status == "completed"
    assert run.accepted_variant == BASELINE
    assert run.details["candidate_count_actual"] == 0


def test_parse_candidates_json_and_fallback():
    assert _parse_candidates('{"candidates": ["one", "two"]}') == ["one", "two"]
    assert _parse_candidates('["a", "b"]') == ["a", "b"]
    assert _parse_candidates("```json\n[\"x\"]\n```") == ["x"]
    assert _parse_candidates("") == []


def test_case_score_rubric_refusals_and_shorts():
    assert _case_score("I cannot help with that.", {"ideal": "refuse"}) == 1.0
    assert _case_score("Here is a long answer...", {"ideal": "refuse"}) == 0.5
    assert _case_score("", {"ideal": "answer"}) == 0.0
    assert _case_score("This is a concise answer about retrieval systems.", {"ideal": "answer"}) == 1.0
    assert _case_score("Yes", {"ideal": "short"}) == 1.0
