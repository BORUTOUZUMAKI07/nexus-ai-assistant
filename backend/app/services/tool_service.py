"""
Tool Application Service.
Owns tool execution logging and HITL approval use cases (SRP).
"""
from typing import Any
from uuid import UUID

import structlog
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.tool.repository import ToolRepository
from backend.app.domain.tool.schemas import ToolApprovalRequest
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
        is_user_approved: bool = False,
    ) -> dict[str, Any]:
        """Log tool invocation, execute through safety gateway, update log."""
        call_log = await self._repo.log_tool_call(
            conversation_id=conversation_id,
            tool_name=tool_name,
            input_args=arguments,
            status="running",
        )

        try:
            result = await tool_gateway.execute_tool(
                tool_name=tool_name,
                arguments=arguments,
                user_id=user_id,
                is_user_approved=is_user_approved,
            )
        except Exception as exc:
            # Keep persisted execution history truthful if validation, rate limiting,
            # or an unexpected gateway failure raises before returning a result.
            await self._repo.update_tool_call(
                tool_call_id=call_log.id,
                status="failed",
                error_message=f"Tool execution failed ({type(exc).__name__}).",
            )
            logger.error(
                "tool_execution_failed",
                tool_name=tool_name,
                error_type=type(exc).__name__,
            )
            raise

        result_status = result.get("status", "completed")
        await self._repo.update_tool_call(
            tool_call_id=call_log.id,
            status="failed" if result_status == "error" else result_status,
            output_result=result.get("result"),
            error_message=result.get("error"),
            execution_time_ms=result.get("duration_ms", 0.0),
        )
        logger.info("tool_executed", tool_name=tool_name, status=result_status)
        return result

    async def approve_tool_call(self, approval: ToolApprovalRequest, user_id: UUID) -> dict[str, Any]:
        """Resolve a pending HITL approval request (scoped to the caller's conversations)."""
        call = await self._repo.get_tool_call(approval.tool_call_id, user_id=user_id)
        if not call:
            raise ResourceNotFoundError("ToolCall", str(approval.tool_call_id))
        if call.status != "requires_approval":
            return {
                "status": "conflict",
                "tool_call_id": approval.tool_call_id,
                "message": "This tool call is not awaiting approval or has already been resolved.",
            }

        resolved = await self._repo.resolve_pending_approval(
            tool_call_id=approval.tool_call_id,
            user_id=user_id,
            approved=approval.approved,
        )
        if not resolved:
            return {
                "status": "conflict",
                "tool_call_id": approval.tool_call_id,
                "message": "This approval was already resolved or is no longer pending.",
            }

        return {
            "status": "success",
            "tool_call_id": approval.tool_call_id,
            "approved": approval.approved,
            "reason": approval.reason,
        }
