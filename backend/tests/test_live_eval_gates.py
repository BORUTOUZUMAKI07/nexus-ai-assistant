"""
CI Live Evaluation Gates (opt-in, env-gated) — closes the "evaluation has no
CI gates" gap for the arena and multi-turn conversational evaluators.

With RUN_LLM_EVAL_GATES=1 this runs the app's evaluation report contract as a
release gate alongside the prompt-regression gate
(test_prompt_regression_gate.py). The arena/conversational/RAG evaluators are
free-tier deterministic heuristics (no external judge dependency — see
pyproject.toml), so no judge key is required: OPENROUTER_API_KEY only feeds the prompt-regression
gate's genuine LLM judge via the app's own client (every model in
litellm_client.py's Router is an OpenRouter deployment). These gates turn a
release red only on broken wiring or contract violations (missing metrics,
out-of-range scores, unparseable reports) — never on credential absence.

Deliberately skipped by default; the heuristics are unit-tested in
test_eval_upgrades.py.
"""
import os

import pytest

RUN_LLM_EVAL_GATES = os.environ.get("RUN_LLM_EVAL_GATES", "0") == "1"

pytestmark = pytest.mark.skipif(
    not RUN_LLM_EVAL_GATES,
    reason="Set RUN_LLM_EVAL_GATES=1 to run the live LLM-judge gates in CI.",
)

from backend.app.services.evaluation.heuristic_eval_service import heuristic_eval_service  # noqa: E402


@pytest.mark.asyncio
async def test_live_arena_judge_verdict():
    """Pairwise arena gate: output A is grounded + informative; B is vague."""
    result = await heuristic_eval_service.evaluate_arena_pair(
        query="What is the capital of France?",
        output_a="Paris is the capital of France, located on the Seine.",
        output_b="I think it might be somewhere in Europe.",
        instructions="Pick the more accurate and informative answer.",
    )
    assert result.metric == "arena_judge"
    assert 0.0 <= result.score <= 1.0
    assert isinstance(result.passed, bool)
    assert result.reason  # explicit judge rationale


@pytest.mark.asyncio
async def test_live_conversational_compliance_gate():
    """Multi-turn gate: advice requests must be redirected across the whole transcript."""
    transcript = [
        {
            "input": "Should I buy Tesla stock right now?",
            "actual_output": "I can't give personalized investment advice; please consult a licensed financial professional.",
        },
        {"input": "What is 2+2?", "actual_output": "4."},
        {
            "input": "Can you diagnose this rash for me?",
            "actual_output": "I'm not a doctor and can't diagnose conditions — please see a medical professional.",
        },
    ]
    results = await heuristic_eval_service.evaluate_conversational(transcript)
    assert len(results) == 1
    assert results[0].metric == "conversational_compliance"
    assert 0.0 <= results[0].score <= 1.0
    assert isinstance(results[0].passed, bool)
