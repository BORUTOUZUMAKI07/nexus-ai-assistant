"""
LLM-as-judge regression gate.

Runs golden-set questions through the assistant and scores answers with the
judge model. Fails CI if any score drops below the configured minimum --
preventing model/provider changes from silently degrading answer quality.

Gated by RUN_LLM_EVAL_GATES env var so it does not run on every PR unless
explicitly enabled (judge calls cost money).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from backend.app.services.evaluation.llm_judge import judge_answer

GOLDEN_SET = Path(__file__).parent / "golden_set.jsonl"


def _load_golden_set() -> list[dict]:
    rows = []
    with open(GOLDEN_SET, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rows.append(json.loads(line))
    return rows


@pytest.mark.skipif(
    os.getenv("RUN_LLM_EVAL_GATES") != "1",
    reason="LLM eval gates only run when RUN_LLM_EVAL_GATES=1",
)
class TestLLMJudgeRegression:
    """Regression tests using the judge model as the oracle."""

    @pytest.mark.parametrize("case", _load_golden_set())
    def test_answer_quality_above_threshold(self, case: dict) -> None:
        question = case["question"]
        min_score = case["min_overall_score"]

        # Route through the actual assistant stream to get a real answer.
        from backend.app.services.run_executor import stream_run

        answer_parts = []
        for frame in stream_run(
            user_message=question,
            conversation_id=None,
            mode="fast_chat",
        ):
            if frame.get("type") == "text_delta":
                answer_parts.append(frame.get("content", ""))
            if frame.get("type") == "done":
                break

        answer = "".join(answer_parts)
        assert answer, f"No answer produced for: {question}"

        result = judge_answer(
            question=question,
            answer=answer,
            sources=[],  # real sources would come from the RAG pipeline
        )

        assert (
            result.overall >= min_score
        ), (
            f"Answer quality {result.overall} below threshold {min_score} "
            f"for question: {question}. Reasons: {result.reasons}"
        )
