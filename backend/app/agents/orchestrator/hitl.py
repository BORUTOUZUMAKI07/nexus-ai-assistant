"""
Human-In-The-Loop (HITL) Service for LangGraph.
Uses official LangGraph `interrupt` and `Command` primitives
to pause execution for human verification on high-stakes tool calls.
"""
from typing import Any

import structlog
from langgraph.types import Command, interrupt

logger = structlog.get_logger(__name__)


def request_human_approval(
    tool_name: str,
    arguments: dict[str, Any],
    reason: str = "This tool call requires explicit user confirmation before proceeding.",
) -> dict[str, Any]:
    """
    Suspends graph execution and prompts the user for approval.
    When the user submits their decision via the API, the graph resumes
    with the decision payload provided in Command(resume=decision).
    """
    logger.info("requesting_hitl_approval", tool_name=tool_name, arguments=arguments)

    approval_payload = {
        "action": "tool_approval",
        "tool_name": tool_name,
        "arguments": arguments,
        "reason": reason,
    }

    # interrupt pauses graph execution and yields the payload to the caller/client
    user_decision: dict[str, Any] = interrupt(approval_payload)

    logger.info("hitl_approval_resumed", user_decision=user_decision)
    return user_decision


def resume_with_approval(approved: bool, reason: str = "") -> Command:
    """
    Constructs the Command to resume the interrupted graph run.
    """
    return Command(
        resume={
            "approved": approved,
            "reason": reason,
        }
    )
