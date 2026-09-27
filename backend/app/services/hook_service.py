"""
Hook Application Service — lifecycle policy CRUD plus registry reloads (SRP).

Every mutation re-snapshots the in-memory HookRegistry so the gateway has fresh
policy within the same request. ``reload_registry`` is also invoked at app
startup (main.py lifespan) so a restart never leaves the registry empty.
"""
from uuid import UUID

import structlog
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.hook.models import HookPolicy
from backend.app.domain.hook.repository import HookRepository
from backend.app.domain.hook.schemas import HookPolicyCreate, HookPolicyUpdate
from backend.app.services.tools.hook_registry import hook_registry
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class HookService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = HookRepository(session)
        self._session = session

    # ── reads ────────────────────────────────────────────────────────────────

    async def list_policies(self, limit: int = 200) -> list[HookPolicy]:
        return await self._repo.list_policies(limit=limit)

    async def get_policy(self, policy_id: UUID) -> HookPolicy:
        policy = await self._repo.get_by_id(policy_id)
        if not policy:
            raise ResourceNotFoundError("HookPolicy", str(policy_id))
        return policy

    async def resolve_org_id(self, user_id: UUID) -> UUID | None:
        """First org membership of the user (hooks scope against a single org).

        Returns None for users without an org — only global (org_id=NULL)
        policies apply to them. Delegates to OrganizationService so multi-tenant
        scoping (hooks, rate limits, usage rollups) shares one resolver.
        """
        from backend.app.services.org_service import OrganizationService

        return await OrganizationService(self._session).resolve_org_id(user_id)

    # ── writes (each mutation reloads the gateway snapshot) ───────────────────

    async def create_policy(self, create: HookPolicyCreate) -> HookPolicy:
        policy = await self._repo.create_policy(create.model_dump())
        await self.reload_registry()
        logger.info("hook_policy_created", policy_id=str(policy.id), name=policy.name)
        return policy

    async def update_policy(self, policy_id: UUID, update: HookPolicyUpdate) -> HookPolicy:
        policy = await self.get_policy(policy_id)
        patch = {k: v for k, v in update.model_dump().items() if v is not None}
        policy = await self._repo.update_policy(policy, patch)
        await self.reload_registry()
        logger.info("hook_policy_updated", policy_id=str(policy.id))
        return policy

    async def delete_policy(self, policy_id: UUID) -> None:
        policy = await self.get_policy(policy_id)
        await self._repo.delete_policy(policy)
        await self.reload_registry()
        logger.info("hook_policy_deleted", policy_id=str(policy_id))

    async def delete_for_org(self, org_id: UUID) -> None:
        """Delete org-scoped policies when an organization is removed."""
        await self._repo.delete_for_org(org_id)
        await self.reload_registry()

    # ── registry sync ────────────────────────────────────────────────────────

    async def reload_registry(self) -> int:
        policies = await self._repo.list_policies()
        hook_registry.set_snapshot(policies)
        return len(policies)
