"""
Critic Subagent.
Evaluates agent outputs against task contracts, criteria rubrics,
and checks for factual inconsistencies or logical errors.
"""
import re
from typing import Any

import structlog
from backend.app.core.config import settings
from backend.app.infrastructure.ai.litellm_client import ai_client

logger = structlog.get_logger(__name__)

# Anchored to a line start: a substring search accepted a NEEDS_REVISION verdict
# whenever the feedback happened to contain the word "APPROVED".
_STATUS_RE = re.compile(r"^\s*STATUS:\s*(APPROVED|NEEDS_REVISION)\b", re.IGNORECASE | re.MULTILINE)
_SCORE_RE = re.compile(r"^\s*SCORE:\s*(\d{1,3})\b", re.IGNORECASE | re.MULTILINE)

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

        try:
            critique = await ai_client.completion(
                messages=[
                    {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                model=settings.FAST_MODEL,
                temperature=0.1,
                max_tokens=400,
            )
        except Exception as exc:
            # The quality critic is not a safety gate. Letting an outage raise
            # here killed the whole NO_RETRY synthesizer node and lost the
            # answer; treating it as a revision request fed the exception text
            # back into the draft prompt as "critic feedback". Accept the
            # unreviewed draft and record the fact instead.
            logger.warning("critic_evaluation_failed", error=str(exc))
            return {
                "subagent": "critic",
                "approved": True,
                "score": None,
                "critique": "Critic unavailable; draft accepted without review.",
                "error": str(exc),
            }

        status_match = _STATUS_RE.search(critique)
        approved = bool(status_match and status_match.group(1).upper() == "APPROVED")

        score: int | None = None
        score_match = _SCORE_RE.search(critique)
        if score_match:
            parsed_score = int(score_match.group(1))
            if 0 <= parsed_score <= 100:
                score = parsed_score

        return {
            "subagent": "critic",
            "approved": approved,
            "score": score,
            "critique": critique,
            "error": None,
        }


critic_subagent = CriticSubagent()
