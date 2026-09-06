"""
Critic Subagent.
Evaluates agent outputs against task contracts, criteria rubrics,
and checks for factual inconsistencies or logical errors.
"""
from typing import Any

import structlog
from backend.app.infrastructure.ai.litellm_client import ai_client

logger = structlog.get_logger(__name__)

CRITIC_SYSTEM_PROMPT = """You are the Nexus Quality & Verification Critic.
Your role is to rigorously evaluate an AI-generated draft against the user's initial request.
Check for:
1. Completeness (Were all parts of the user request answered?)
2. Accuracy & Hallucinations (Are assertions grounded and factually sound?)
3. Code Quality (Is code secure, idiomatic, and without unhandled edge cases?)
4. Clarity & Formatting (Is the response well-structured and concise?)

Output your evaluation in format:
STATUS: [APPROVED or NEEDS_REVISION]
SCORE: [0-100]
FEEDBACK: [Concise critique or recommendations]"""


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
            model="llama-3.3-70b-versatile",
            temperature=0.1,
        )

        approved = "STATUS: APPROVED" in critique.upper()
        return {
            "subagent": "critic",
            "approved": approved,
            "critique": critique,
        }


critic_subagent = CriticSubagent()
