"""
MCP-Style Elicitations (core MCP primitive) wired into the existing HITL.

An *elicitation* is a structured request for human input mid-task — the
protocol-native sibling of tool approvals. Whereas an approval says "may I run
this tool?", an elicitation says "I need a decision or a piece of structured
(schema-validated) data to continue."

This service persists elicitations as special tool calls
(``tool_name="__elicitation__"``) so they reuse the *verified* single-use atomic
claim machinery (SEC-06): owner-scoped, consumed exactly once, expired per HITL
timeout — with the user's answer stored in ``output_result``.

Only the answer (not the elicitation itself) is ever retrievable after
resolution, and the schema is enforced on the way in so a client cannot stuff
arbitrary payloads into a parked request.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from backend.app.core.config import settings
from backend.app.core.exceptions import ApprovalConsumedError, ResourceNotFoundError
from backend.app.domain.tool.repository import ToolRepository

logger = structlog.get_logger(__name__)

ELICITATION_TOOL_NAME = "__elicitation__"


class ElicitationService:
    """Application service for structured human-input requests."""

    def __init__(self, session, repo=None) -> None:
        # repo injection keeps the service unit-testable without a DB.
        self._repo = repo if repo is not None else ToolRepository(session)

    async def park_elicitation(
        self,
        conversation_id: UUID,
        schema: dict[str, Any],
        message: str,
        title: str = "Action needed",
    ) -> dict[str, Any]:
        """
        Park a structured-input request against a conversation. Returns the
        elicitation id; the caller should surface it to the human via the
        ``elicitation_request`` SSE event or the MCP response.
        """
        call = await self._repo.log_tool_call(
            conversation_id=conversation_id,
            tool_name=ELICITATION_TOOL_NAME,
            input_args={"schema": schema, "message": message, "title": title},
            status="pending",
            requires_approval=True,
        )
        logger.info("elicitation_parked", elicitation_id=str(call.id), conversation_id=str(conversation_id))
        return {
            "elicitation_id": str(call.id),
            "status": "pending",
            "title": title,
            "message": message,
            "schema": schema,
        }

    async def resolve_elicitation(
        self,
        elicitation_id: UUID,
        user_id: UUID,
        answer: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Single-use, owner-scoped, expiry-checked resolution of a pending
        elicitation (mirrors SEC-06 approvals):
        404 when absent/not-owned → 410 when expired → 409 when already
        resolved. On success the schema-validated answer is persisted in
        ``output_result`` and the row is marked completed.
        """
        pending = await self._repo.get_tool_call(elicitation_id, user_id=user_id)
        if not pending or pending.tool_name != ELICITATION_TOOL_NAME:
            raise ResourceNotFoundError("Elicitation", str(elicitation_id))

        created_at = pending.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        timeout = getattr(settings, "HITL_APPROVAL_TIMEOUT_SECONDS", 900)
        if datetime.now(UTC) - created_at > timedelta(seconds=timeout):
            from backend.app.core.exceptions import ApprovalExpiredError

            raise ApprovalExpiredError(message=f"Elicitation {elicitation_id} has expired.")

        claimed = await self._repo.claim_tool_call_for_approval(elicitation_id, user_id, approved=True)
        if claimed is None:
            raise ApprovalConsumedError(message=f"Elicitation {elicitation_id} was already resolved.")

        # Persist only the validated answer — never expose the parked schema/message.
        await self._repo.update_tool_call(
            tool_call_id=elicitation_id,
            status="completed",
            output_result={"answer": answer},
        )
        logger.info("elicitation_resolved", elicitation_id=str(elicitation_id), user_id=str(user_id))
        return {
            "status": "success",
            "elicitation_id": str(elicitation_id),
            "resolved": True,
        }
