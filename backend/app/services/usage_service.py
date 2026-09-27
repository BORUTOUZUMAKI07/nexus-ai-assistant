"""
Usage Application Service.
Owns usage telemetry and evaluation log retrieval use cases (SRP).
"""
from uuid import UUID

import structlog
from backend.app.domain.usage.models import EvaluationLog
from backend.app.domain.usage.repository import UsageRepository
from backend.app.domain.usage.schemas import OrgUsageSummaryResponse, UsageSummaryResponse
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class UsageService:
    """
    Application service for usage statistics and evaluation log use cases.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = UsageRepository(session)

    async def get_summary(self, user_id: UUID) -> UsageSummaryResponse:
        return await self._repo.get_summary(user_id)

    async def get_org_summary(
        self, org_id: UUID, billing_period: str | None = None
    ) -> OrgUsageSummaryResponse:
        """Per-org usage + cost rollup (multi-tenant observability, T-07)."""
        usage = await self._repo.get_org_summary(org_id)
        cost = await self._repo.get_org_cost_summary(org_id, billing_period)
        return OrgUsageSummaryResponse(
            organization_id=org_id,
            billing_period=billing_period,
            total_requests=usage.total_requests,
            total_tokens=usage.total_tokens,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cached_tokens=usage.cached_tokens,
            usage_cost_usd=usage.total_cost_usd,
            cost_entries=int(cost["entries"]),
            cost_input_usd=float(cost["input_cost"]),
            cost_output_usd=float(cost["output_cost"]),
            cost_total_usd=float(cost["total_cost"]),
        )

    async def get_evaluations(
        self, user_id: UUID, conversation_id: UUID | None = None, limit: int = 50
    ) -> list[EvaluationLog]:
        return await self._repo.get_evaluations(
            user_id=user_id, conversation_id=conversation_id, limit=limit
        )
