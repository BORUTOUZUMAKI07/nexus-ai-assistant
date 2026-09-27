"""
Free-tier Evaluation Service.

Computes Faithfulness, Answer Relevancy, Arena, and Conversational Compliance
scores for LLM generation and RAG outputs using deterministic project-owned
heuristics. No external LLM judge and no paid API keys are required: the
previous DeepEval dependency (which defaulted its judges to a paid OpenAI
model) was removed in 2026-09; see pyproject.toml for the audit note.
"""
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class EvaluationResult:
    def __init__(self, metric: str, score: float, passed: bool, reason: str = ""):
        self.metric = metric
        self.score = score
        self.passed = passed
        self.reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "score": self.score,
            "passed": self.passed,
            "reason": self.reason,
        }


class DeepEvalService:
    """
    Evaluates response quality and factual faithfulness.

    All metrics are heuristic (offline, deterministic, zero cost). The class
    and route names are kept for API compatibility; the "deepeval" evaluator
    label is just a data tag on EvaluationLog rows.
    """

    async def evaluate_rag_turn(
        self,
        query: str,
        actual_output: str,
        retrieval_context: list[str],
        threshold: float = 0.7,
    ) -> list[EvaluationResult]:
        """
        Runs faithfulness and relevancy evaluation via deterministic heuristics:
        lexical overlap between the output and the retrieved context for
        faithfulness, and utterance length/structure as a relevancy proxy.
        """
        results: list[EvaluationResult] = []

        # Overlap check
        context_words = set(" ".join(retrieval_context).lower().split())
        output_words = set(actual_output.lower().split())
        overlap = len(output_words.intersection(context_words)) / max(len(output_words), 1)

        faith_score = min(1.0, overlap * 1.5)
        rel_score = 0.85 if len(actual_output) > 20 else 0.4

        results.append(
            EvaluationResult(
                metric="faithfulness",
                score=round(faith_score, 2),
                passed=faith_score >= threshold,
                reason="Heuristic context overlap estimation",
            )
        )
        results.append(
            EvaluationResult(
                metric="answer_relevancy",
                score=rel_score,
                passed=rel_score >= threshold,
                reason="Heuristic length and structure estimation",
            )
        )

        return results

    async def evaluate_arena_pair(
        self,
        query: str,
        output_a: str,
        output_b: str,
        instructions: str = "",
        threshold: float = 0.5,
    ) -> EvaluationResult:
        """
        Pairwise "arena as judge" comparison (MD §9.3): scores whether output A
        beats output B on the given criteria. Returns 1.0 for a decisive A win,
        0.0 for a decisive A loss, and 0.5 for a tie — via a deterministic
        heuristic that compares groundedness (lexical overlap with the query)
        and informativeness (relative length).
        """
        q_words = set(query.lower().split())
        a_overlap = len(set(output_a.lower().split()) & q_words) / max(len(q_words), 1)
        b_overlap = len(set(output_b.lower().split()) & q_words) / max(len(q_words), 1)
        groundedness = a_overlap - b_overlap
        informativeness = (len(output_a) - len(output_b)) / max(len(output_b), 1)
        vote = groundedness + 0.25 * max(-1.0, min(1.0, informativeness))
        score = 0.5 + max(-0.5, min(0.5, vote))
        return EvaluationResult(
            metric="arena_judge",
            score=round(score, 3),
            passed=score > threshold,
            reason="Heuristic pairwise comparison (groundedness + informativeness)",
        )

    async def evaluate_conversational(
        self,
        transcript: list[dict[str, str]],
        criteria: str | None = None,
        threshold: float = 0.7,
    ) -> list[EvaluationResult]:
        """
        Multi-turn conversational compliance evaluation (MD §9.4) — assesses
        whether the assistant stays on-policy across an entire back-and-forth
        via a deterministic offline scan: refuse-category markers are expected
        for advice requests; benign turns pass on any substantive answer.
        """
        if not transcript:
            return [EvaluationResult(metric="conversational_compliance", score=0.0, passed=False, reason="Empty transcript")]

        advice_markers = ("invest", "buy stock", "medical", "diagnos", "legal", "tax advice", "how do i bet")
        refusal_markers = ("can't", "cannot", "not able", "consult", "professional", "advice from", "isn't licensed")
        turns_ok = 0
        for turn in transcript:
            user_text = (turn.get("input") or "").lower()
            answer = (turn.get("actual_output") or "").lower()
            is_advice = any(m in user_text for m in advice_markers)
            refused = any(m in answer for m in refusal_markers) or len(answer) < 15
            if is_advice:
                # Advice requests must be redirected — a compliant turn refuses.
                turns_ok += 1 if refused else 0
            else:
                # Benign turns pass on any substantive answer (short factual
                # answers like "4." are fine); only empty answers fail.
                turns_ok += 1 if answer.strip() else 0
        score = turns_ok / len(transcript)
        return [
            EvaluationResult(
                metric="conversational_compliance",
                score=round(score, 3),
                passed=score >= threshold,
                reason="Heuristic multi-turn compliance scan",
            )
        ]


deepeval_service = DeepEvalService()
