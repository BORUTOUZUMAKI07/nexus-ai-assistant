"""
Repository for HookPolicy domain operations.
"""
from datetime import UTC, datetime
from uuid import UUID

from backend.app.domain.base_repository import BaseRepository
from backend.app.domain.hook.models import HookPolicy
from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession


class HookRepository(BaseRepository[HookPolicy]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, HookPolicy)

    async def list_policies(self, limit: int = 200) -> list[HookPolicy]:
        statement = select(HookPolicy).order_by(HookPolicy.created_at.asc()).limit(limit)
        result = await self.session.exec(statement)
        return list(result.all())

    async def get_by_id(self, policy_id: UUID) -> HookPolicy | None:
        result = await self.session.exec(select(HookPolicy).where(HookPolicy.id == policy_id))
        return result.first()

    async def create_policy(self, payload: dict) -> HookPolicy:
        policy = HookPolicy(**payload)
        self.session.add(policy)
        await self.session.commit()
        await self.session.refresh(policy)
        return policy

    async def update_policy(self, policy: HookPolicy, patch: dict) -> HookPolicy:
        for key, value in patch.items():
            if value is not None and hasattr(policy, key):
                setattr(policy, key, value)
        policy.updated_at = datetime.now(UTC).replace(tzinfo=None)
        self.session.add(policy)
        await self.session.commit()
        await self.session.refresh(policy)
        return policy

    async def delete_policy(self, policy: HookPolicy) -> None:
        await self.session.delete(policy)
        await self.session.commit()

    async def delete_for_org(self, org_id: UUID) -> None:
        """Remove org-scoped policies when an organization is deleted."""
        await self.session.exec(delete(HookPolicy).where(HookPolicy.org_id == org_id))
