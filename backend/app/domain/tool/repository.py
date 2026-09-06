"""
Repository for Tool domain operations.
"""
from datetime import datetime
from uuid import UUID

from backend.app.domain.base_repository import BaseRepository
from backend.app.domain.tool.models import Tool, ToolCall
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession


class ToolRepository(BaseRepository[Tool]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Tool)

    async def get_all(self, enabled_only: bool = True) -> list[Tool]:
        statement = select(Tool)
        if enabled_only:
            statement = statement.where(Tool.is_enabled == True)
        result = await self.session.exec(statement)
        return list(result.all())

    async def get_by_name(self, name: str) -> Tool | None:
        statement = select(Tool).where(Tool.name == name)
        result = await self.session.exec(statement)
        return result.first()

    async def register_tool(
        self,
        name: str,
        description: str,
        category: str = "general",
        parameters_schema: dict | None = None,
        requires_approval: bool = False,
        timeout_seconds: int = 30,
        is_system: bool = True,
    ) -> Tool:
        existing = await self.get_by_name(name)
        if existing:
            existing.description = description
            existing.category = category
            existing.parameters_schema = parameters_schema or {}
            existing.requires_approval = requires_approval
            existing.timeout_seconds = timeout_seconds
            existing.updated_at = datetime.utcnow()
            self.session.add(existing)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing

        tool = Tool(
            name=name,
            description=description,
            category=category,
            parameters_schema=parameters_schema or {},
            requires_approval=requires_approval,
            timeout_seconds=timeout_seconds,
            is_system=is_system,
        )
        self.session.add(tool)
        await self.session.commit()
        await self.session.refresh(tool)
        return tool

    async def log_tool_call(
        self,
        conversation_id: UUID,
        tool_name: str,
        input_args: dict,
        message_id: UUID | None = None,
        status: str = "pending",
        requires_approval: bool = False,
    ) -> ToolCall:
        call = ToolCall(
            conversation_id=conversation_id,
            message_id=message_id,
            tool_name=tool_name,
            input_args=input_args,
            status=status,
            requires_approval=requires_approval,
        )
        self.session.add(call)
        await self.session.commit()
        await self.session.refresh(call)
        return call

    async def update_tool_call(
        self,
        tool_call_id: UUID,
        status: str,
        output_result: dict | None = None,
        error_message: str | None = None,
        execution_time_ms: float = 0.0,
        is_approved: bool | None = None,
    ) -> ToolCall | None:
        statement = select(ToolCall).where(ToolCall.id == tool_call_id)
        result = await self.session.exec(statement)
        call = result.first()
        if call:
            call.status = status
            if output_result is not None:
                call.output_result = output_result
            if error_message is not None:
                call.error_message = error_message
            if execution_time_ms > 0:
                call.execution_time_ms = execution_time_ms
            if is_approved is not None:
                call.is_approved = is_approved
            self.session.add(call)
            await self.session.commit()
            await self.session.refresh(call)
        return call

    async def get_tool_call(self, tool_call_id: UUID) -> ToolCall | None:
        statement = select(ToolCall).where(ToolCall.id == tool_call_id)
        result = await self.session.exec(statement)
        return result.first()
