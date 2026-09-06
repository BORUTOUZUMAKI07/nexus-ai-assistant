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

        result = await tool_gateway.execute_tool(
            tool_name=tool_name,
            arguments=arguments,
            user_id=user_id,
            is_user_approved=is_user_approved,
        )

        await self._repo.update_tool_call(
            tool_call_id=call_log.id,
            status=result.get("status", "completed"),
            output_result=result.get("result"),
            error_message=result.get("error"),
            execution_time_ms=result.get("duration_ms", 0.0),
        )
        logger.info("tool_executed", tool_name=tool_name, status=result.get("status"))
        return result

    async def approve_tool_call(self, approval: ToolApprovalRequest) -> dict[str, Any]:
        """Resolve a pending HITL approval request."""
        call = await self._repo.get_tool_call(approval.tool_call_id)
        if not call:
            raise ResourceNotFoundError("ToolCall", str(approval.tool_call_id))

        await self._repo.update_tool_call(
            tool_call_id=approval.tool_call_id,
            status="approved" if approval.approved else "rejected",
            is_approved=approval.approved,
        )
        return {
            "status": "success",
            "tool_call_id": approval.tool_call_id,
            "approved": approval.approved,
            "reason": approval.reason,
        }
