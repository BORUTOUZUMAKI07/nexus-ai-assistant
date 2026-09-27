"""
Repository for Plan domain operations.
"""
from datetime import UTC, datetime
from uuid import UUID

from backend.app.domain.base_repository import BaseRepository
from backend.app.domain.plan.models import Plan
from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession


class PlanRepository(BaseRepository[Plan]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Plan)

    async def create_plan(
        self,
        conversation_id: UUID,
        user_id: UUID,
        title: str,
        summary: str | None,
        steps: list[str],
    ) -> Plan:
        plan = Plan(
            conversation_id=conversation_id,
            user_id=user_id,
            title=title,
            summary=summary,
            steps=steps,
            status="pending",
        )
        self.session.add(plan)
        await self.session.commit()
        await self.session.refresh(plan)
        return plan

    async def get_by_id(self, plan_id: UUID, user_id: UUID | None = None) -> Plan | None:
        """Fetch a plan, optionally owner-scoped (IDOR guard)."""
        statement = select(Plan).where(Plan.id == plan_id)
        if user_id:
            statement = statement.where(Plan.user_id == user_id)
        result = await self.session.exec(statement)
        return result.first()

    async def list_for_conversation(
        self, conversation_id: UUID, user_id: UUID, limit: int = 20
    ) -> list[Plan]:
        statement = (
            select(Plan)
            .where(
                (Plan.conversation_id == conversation_id)
                & (Plan.user_id == user_id)
            )
            .order_by(Plan.created_at.desc())
            .limit(limit)
        )
        result = await self.session.exec(statement)
        return list(result.all())

    async def update_status(
        self,
        plan: Plan,
        status: str,
        reason: str | None = None,
    ) -> Plan:
        plan.status = status
        if reason is not None:
            plan.decision_reason = reason
        plan.decided_at = datetime.now(UTC).replace(tzinfo=None)
        plan.updated_at = datetime.now(UTC).replace(tzinfo=None)
        self.session.add(plan)
        await self.session.commit()
        await self.session.refresh(plan)
        return plan

    async def delete_for_conversation(self, conversation_id: UUID) -> None:
        """Remove every plan of a conversation (conversation-delete cascade)."""
        await self.session.exec(
            delete(Plan).where(Plan.conversation_id == conversation_id)
        )

    async def delete_for_user(self, user_id: UUID) -> None:
        """Remove every plan a user owns (account-erasure cascade)."""
        await self.session.exec(delete(Plan).where(Plan.user_id == user_id))
