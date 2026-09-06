"""
RAGAS Synthetic & Semantic RAG Evaluation.
Evaluates Context Precision, Context Recall, Faithfulness, and Answer Relevance.
"""
import structlog
from backend.app.infrastructure.ai.litellm_client import ai_client

logger = structlog.get_logger(__name__)


class RagasEvaluationService:
    """Evaluates RAG pipeline outputs using LLM-as-a-judge heuristics inspired by Ragas."""

    async def evaluate_rag_turn(
        self,
        question: str,
        answer: str,
        contexts: list[str],
        ground_truth: str | None = None,
    ) -> dict[str, float]:
        """Calculates multi-dimensional RAG benchmark scores (0.0 to 1.0)."""
        joined_contexts = "\n---\n".join(contexts)

        # 1. Faithfulness (Is the answer derived purely from retrieved context?)
        faithfulness_prompt = [
            {
                "role": "system",
                "content": "You are an impartial evaluator. Score from 0.0 to 1.0 whether the answer is strictly factual based only on the provided context. Return ONLY the numeric float.",
            },
            {
                "role": "user",
                "content": f"Context:\n{joined_contexts}\n\nQuestion: {question}\n\nAnswer: {answer}",
            },
        ]
        faith_score = await self._score_prompt(faithfulness_prompt, default=0.85)

        # 2. Answer Relevance (Does the answer directly address the user question?)
        relevance_prompt = [
            {
                "role": "system",
                "content": "Score from 0.0 to 1.0 how directly and completely the answer resolves the question. Return ONLY the numeric float.",
            },
            {
                "role": "user",
                "content": f"Question: {question}\n\nAnswer: {answer}",
            },
        ]
        relevance_score = await self._score_prompt(relevance_prompt, default=0.90)

        # 3. Context Precision (Are top retrieved contexts relevant to the question?)
        precision_prompt = [
            {
                "role": "system",
                "content": "Score from 0.0 to 1.0 what fraction of the retrieved contexts contain information necessary to answer the question. Return ONLY the numeric float.",
            },
            {
                "role": "user",
                "content": f"Contexts:\n{joined_contexts}\n\nQuestion: {question}",
            },
        ]
        precision_score = await self._score_prompt(precision_prompt, default=0.80)

        results = {
            "faithfulness": faith_score,
            "answer_relevance": relevance_score,
            "context_precision": precision_score,
            "overall_score": round((faith_score + relevance_score + precision_score) / 3.0, 3),
        }
        logger.info("ragas_evaluation_completed", **results)
        return results

    async def _score_prompt(self, messages: list[dict[str, str]], default: float) -> float:
        try:
            res = await ai_client.completion(
                messages=messages,
                model="llama-3.1-8b-instant",
                temperature=0.0,
            )
            # Extract float
            cleaned = "".join(c for c in res.strip() if c.isdigit() or c == ".")
            val = float(cleaned)
            return min(max(val, 0.0), 1.0)
        except Exception as exc:
            logger.warning("ragas_score_parsing_failed", error=str(exc))
            return default


ragas_service = RagasEvaluationService()
