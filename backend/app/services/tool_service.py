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
    ToolPermissionError,
)
from backend.app.domain.tool.repository import ToolRepository
from backend.app.domain.tool.schemas import ToolApprovalRequest
from backend.app.services.hook_service import HookService
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
        self._session = session

    async def _resolve_org_id(self, user_id: UUID) -> UUID | None:
        """Best-effort org scope for lifecycle hooks (single-org model)."""
        try:
            return await HookService(self._session).resolve_org_id(user_id)
        except Exception as exc:
            logger.warning("hook_org_resolution_failed", error=str(exc))
            return None

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

        try:
            result = await tool_gateway.execute_tool(
                tool_name=tool_name,
                arguments=arguments,
                user_id=user_id,
                org_id=await self._resolve_org_id(user_id),
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

        if result.get("status") == "blocked":
            await self._repo.update_tool_call(
                tool_call_id=call_log.id,
                status="blocked",
                error_message=result.get("message"),
            )
            raise ToolPermissionError(
                message=result.get("message") or "Tool execution blocked by policy."
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
        supplies the id of the exact pending call plus a yes/no. The row is
        resolved atomically (single-use) to approved/rejected; the orchestrator
        executes approved calls with a server-set flag — never a client-supplied
        approval flag or altered arguments. Stale requests are rejected.
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

        if pending.status != "requires_approval":
            return {
                "status": "conflict",
                "tool_call_id": approval.tool_call_id,
                "message": "This tool call is not awaiting approval or has already been resolved.",
            }

        if approval.approved:
            created_at = pending.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            timeout = getattr(settings, "HITL_APPROVAL_TIMEOUT_SECONDS", 900)
            if datetime.now(UTC) - created_at > timedelta(seconds=timeout):
                raise ApprovalExpiredError(
                    message=f"Approval for tool call {approval.tool_call_id} has expired."
                )

        # Atomic single-use resolution: only the first resolver transitions the
        # row (status -> approved/rejected). The orchestrator executes approved
        # calls server-side with a server-set approval flag — the approving
        # client never triggers execution or alters the logged arguments.
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
            "tool_call_id": str(approval.tool_call_id),
            "approved": approval.approved,
            "reason": approval.reason,
        }
