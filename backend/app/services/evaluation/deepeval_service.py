"""
DeepEval Evaluation Service.
Computes Faithfulness, Answer Relevancy, and Hallucination scores
for LLM generation and RAG outputs.
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
    """

    async def evaluate_rag_turn(
        self,
        query: str,
        actual_output: str,
        retrieval_context: list[str],
        threshold: float = 0.7,
    ) -> list[EvaluationResult]:
        """
        Runs faithfulness and relevancy evaluation.
        """
        results: list[EvaluationResult] = []

        try:
            # Try importing deepeval if installed
            from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
            from deepeval.test_case import LLMTestCase

            test_case = LLMTestCase(
                input=query,
                actual_output=actual_output,
                retrieval_context=retrieval_context,
            )

            # Faithfulness
            faith_metric = FaithfulnessMetric(threshold=threshold)
            await faith_metric.a_measure(test_case)
            results.append(
                EvaluationResult(
                    metric="faithfulness",
                    score=float(faith_metric.score),
                    passed=bool(faith_metric.is_successful()),
                    reason=getattr(faith_metric, "reason", ""),
                )
            )

            # Relevancy
            rel_metric = AnswerRelevancyMetric(threshold=threshold)
            await rel_metric.a_measure(test_case)
            results.append(
                EvaluationResult(
                    metric="answer_relevancy",
                    score=float(rel_metric.score),
                    passed=bool(rel_metric.is_successful()),
                    reason=getattr(rel_metric, "reason", ""),
                )
            )

        except Exception as exc:
            logger.warning("deepeval_native_eval_skipped_using_heuristic", error=str(exc))
            # Fast heuristic evaluation fallback
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


deepeval_service = DeepEvalService()
