"""
Human-In-The-Loop (HITL) Service for LangGraph.
Uses official LangGraph `interrupt` and `Command` primitives
to pause execution for human verification on high-stakes tool calls.
"""
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from backend.app.core.config import settings
from langgraph.types import interrupt

logger = structlog.get_logger(__name__)


def approval_timeout_seconds() -> int:
    return getattr(settings, "HITL_APPROVAL_TIMEOUT_SECONDS", 900)


def build_approval_payload(
    tool_name: str,
    arguments: dict[str, Any],
    reason: str = "This tool call requires explicit user confirmation before proceeding.",
) -> dict[str, Any]:
    """
    Payload handed to the caller while the graph is interrupted.
    Carries an ``approval_deadline`` (UTC ISO-8601) so a parked approval can be
    auto-expired; the resume endpoint refuses to resume a stale request.
    """
    return {
        "action": "tool_approval",
        "tool_name": tool_name,
        "arguments": arguments,
        "reason": reason,
        "approval_deadline": (datetime.now(UTC) + timedelta(seconds=approval_timeout_seconds())).isoformat(),
    }


def is_approval_expired(payload: dict[str, Any] | None) -> bool:
    """True when the interrupt payload's deadline has passed (or is unparseable)."""
    if not payload or not isinstance(payload, dict):
        return False
    deadline = payload.get("approval_deadline")
    if not deadline:
        return False
    try:
        deadline_dt = datetime.fromisoformat(str(deadline))
    except ValueError:
        return True
    if deadline_dt.tzinfo is None:
        deadline_dt = deadline_dt.replace(tzinfo=UTC)
    return datetime.now(UTC) > deadline_dt


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
    # Tool arguments may contain credentials, personal data, or other secrets.
    # Keep logs to non-sensitive metadata; the approval payload is sent only
    # through the authenticated graph interrupt channel.
    logger.info("requesting_hitl_approval", tool_name=tool_name, argument_count=len(arguments))

    approval_payload = build_approval_payload(tool_name, arguments, reason)

    # interrupt pauses graph execution and yields the payload to the caller/client
    user_decision: dict[str, Any] = interrupt(approval_payload)

    decision_action = user_decision.get("action") if isinstance(user_decision, dict) else None
    logger.info("hitl_approval_resumed", action=decision_action)
    return user_decision
