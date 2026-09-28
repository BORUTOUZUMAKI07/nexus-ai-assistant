"""
CI Prompt-Regression Gate (opt-in, env-gated).

The "CI stopped" equivalent for prompt changes (industry Annex exercise):
with RUN_LLM_EVAL_GATES=1, validates that the *currently compiled* system
prompt still passes the guardrail golden set before a release proceeds.

Deliberately skipped by default: the offline heuristic gate is covered in
test_eval_upgrades.py; this test is the live-judge variant meant for CI with
real model credentials.
"""
import os

import pytest

RUN_LLM_EVAL_GATES = os.environ.get("RUN_LLM_EVAL_GATES", "0") == "1"

pytestmark = pytest.mark.skipif(
    not RUN_LLM_EVAL_GATES,
    reason="Set RUN_LLM_EVAL_GATES=1 to run the live prompt-regression gate in CI.",
)


@pytest.mark.asyncio
async def test_compiled_system_prompt_passes_golden_gate():
    from backend.app.services.evaluation.regression_service import prompt_regression_gate
    from backend.app.services.prompt_compiler import prompt_compiler

    system_prompt = await prompt_compiler.compile_system_prompt_cached()
    report = await prompt_regression_gate.evaluate_prompt(system_prompt=system_prompt)
    assert report["gate"] == "passed", report
    assert not report["critical_failures"], report["critical_failures"]
