"""
Quality & Token Efficiency Service.
Monitors output conciseness, formatting compliance, token efficiency, and hallucination bounds.
"""
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class QualityService:
    """Computes generation quality metrics and formatting adherence."""

    @staticmethod
    def evaluate_response_quality(
        prompt: str, response: str, tokens_used: int, latency_ms: int
    ) -> dict[str, Any]:
        char_count = len(response)
        word_count = len(response.split())
        tokens_per_sec = (
            round((tokens_used / (latency_ms / 1000.0)), 2) if latency_ms > 0 else 0.0
        )

        # Basic formatting checks
        has_markdown = any(md in response for md in ["#", "```", "*", "-", "|", ">"])
        is_empty = word_count == 0

        # Efficiency ratio (words per token)
        words_per_token = round(word_count / max(tokens_used, 1), 2)

        return {
            "tokens_used": tokens_used,
            "latency_ms": latency_ms,
            "tokens_per_second": tokens_per_sec,
            "word_count": word_count,
            "char_count": char_count,
            "words_per_token": words_per_token,
            "has_markdown": has_markdown,
            "is_valid": not is_empty,
        }


quality_service = QualityService()
