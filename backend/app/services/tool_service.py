"""
Tool Application Service.
Owns tool execution logging and HITL approval use cases (SRP).
"""
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from backend.app.core.config import settings
from backend.app.core.exceptions import (
    ApprovalConsumedError,
    ApprovalExpiredError,
    ResourceNotFoundError,
)
from backend.app.domain.tool.repository import ToolRepository
from backend.app.domain.tool.schemas import ToolApprovalRequest
from backend.app.services.tools.elicitations import ELICITATION_TOOL_NAME
from backend.app.services.tools.tool_gateway import tool_gateway
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class ToolService:
    """
    Application service for tool execution, call logging, and HITL approval.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = ToolRepository(session)

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        conversation_id: UUID,
        user_id: UUID,
    ) -> dict[str, Any]:
        """Log tool invocation, execute through safety gateway, update log.

        ``is_user_approved`` is deliberately NOT a parameter: the public path
        cannot supply an approval flag. Approval-required tools return
        ``status=requires_approval`` with the logged ``tool_call_id`` so the
        human can approve that exact call via ``approve_tool_call``, which is
        the only place the server sets the approval and executes afterwards.
        """
        call_log = await self._repo.log_tool_call(
            conversation_id=conversation_id,
            tool_name=tool_name,
            input_args=arguments,
            status="running",
        )

        result = await tool_gateway.execute_tool(
            tool_name=tool_name,
            arguments=arguments,
            user_id=user_id,
        )

        await self._repo.update_tool_call(
            tool_call_id=call_log.id,
            status=result.get("status", "completed"),
            output_result=result.get("result"),
            error_message=result.get("error"),
            execution_time_ms=result.get("duration_ms", 0.0),
        )
        if result.get("status") == "requires_approval":
            # Hand the caller the id of the exact pending call so it can be
            # approved (SEC-03/SEC-06: approval binds to this specific call).
            result["tool_call_id"] = str(call_log.id)
        logger.info("tool_executed", tool_name=tool_name, status=result.get("status"))
        return result

    async def approve_tool_call(self, approval: ToolApprovalRequest, user_id: UUID) -> dict[str, Any]:
        """Resolve a pending HITL approval request (scoped to the caller's conversations).

        Server-side approval consumption (SEC-02/SEC-05/SEC-06): the client only
        supplies the id of the exact pending call plus a yes/no. On approval the
        server executes the *logged* tool with the *logged* arguments — never a
        client-supplied approval flag or altered arguments. The row is claimed
        atomically (single-use) and stale requests are rejected.
        """
        # Owner-scoped existence + expiry check (before the atomic claim).
        pending = await self._repo.get_tool_call(approval.tool_call_id, user_id=user_id)
        if not pending:
            raise ResourceNotFoundError("ToolCall", str(approval.tool_call_id))

        if pending.tool_name == ELICITATION_TOOL_NAME:
            # Elicitations are structured human-input requests, not tool runs:
            # approving them via /tools/approval would attempt to "execute" a
            # synthetic tool. Route through the elicitation endpoint instead.
            raise ApprovalConsumedError(
                message="Elicitations are resolved via POST /tools/elicitations/{id}/respond, not /tools/approval."
            )

        if approval.approved:
            created_at = pending.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            timeout = getattr(settings, "HITL_APPROVAL_TIMEOUT_SECONDS", 900)
            if datetime.now(UTC) - created_at > timedelta(seconds=timeout):
                raise ApprovalExpiredError(
                    message=f"Approval for tool call {approval.tool_call_id} has expired."
                )

        # Atomic single-use claim: only the first resolver gets the row.
        call = await self._repo.claim_tool_call_for_approval(
            approval.tool_call_id, user_id, approved=approval.approved
        )
        if call is None:
            raise ApprovalConsumedError(
                message=f"Approval for tool call {approval.tool_call_id} was already resolved."
            )

        if not approval.approved:
            return {
                "status": "success",
                "tool_call_id": str(approval.tool_call_id),
                "approved": False,
                "reason": approval.reason,
            }

        # Server-side execution of the exact logged call (is_user_approved is set
        # by server code here — post-approval — never by the requester).
        result = await tool_gateway.execute_tool(
            tool_name=call.tool_name,
            arguments=call.input_args,
            user_id=user_id,
            is_user_approved=True,
        )
        await self._repo.update_tool_call(
            tool_call_id=approval.tool_call_id,
            status=result.get("status", "completed"),
            output_result=result.get("result"),
            error_message=result.get("error"),
            execution_time_ms=result.get("duration_ms", 0.0),
        )
        return {
            "status": "success",
            "tool_call_id": str(approval.tool_call_id),
            "approved": True,
            "execution": result,
        }
