"""
Critic Subagent.
Evaluates agent outputs against task contracts, criteria rubrics,
and checks for factual inconsistencies or logical errors.
"""
from typing import Any

import structlog
from backend.app.core.config import settings
from backend.app.infrastructure.ai.litellm_client import ai_client

logger = structlog.get_logger(__name__)

CRITIC_SYSTEM_PROMPT = """You are the Nexus Quality & Verification Critic. Evaluate an AI draft strictly against the user's original request.

Grade on four axes:
1. Completeness - every part of the request is answered; nothing promised and then missing.
2. Accuracy & grounding - assertions are supported and factual; no hallucinations, invented sources, or unverifiable numbers.
3. Code quality - when code is present: secure, idiomatic, edge cases handled, no dead or placeholder paths.
4. Clarity & formatting - well-structured, concise, and readable; the format fits the content.

Be rigorous: an answer that is pleasant but wrong must fail, and a correct answer must pass even if it is plain.

Output exactly this format:
STATUS: [APPROVED or NEEDS_REVISION]
SCORE: [0-100]
FEEDBACK: [concise critique - what is wrong, or what to keep as-is]"""


class CriticSubagent:
    """
    Subagent that audits answers before they are finalized.
    """

    async def evaluate(self, user_request: str, candidate_response: str) -> dict[str, Any]:
        logger.info("critic_evaluation_starting")

        prompt = (
            f"User Request:\n{user_request}\n\n"
            f"Candidate AI Response:\n{candidate_response}\n\n"
            "Evaluate this response strictly according to your rubric."
        )

        critique = await ai_client.completion(
            messages=[
                {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            model=settings.FAST_MODEL,
            temperature=0.1,
            max_tokens=400,
        )

        approved = "STATUS: APPROVED" in critique.upper()
        return {
            "subagent": "critic",
            "approved": approved,
            "critique": critique,
        }


critic_subagent = CriticSubagent()
