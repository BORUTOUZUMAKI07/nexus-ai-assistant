"""
Usage Application Service.
Owns usage telemetry and evaluation log retrieval use cases (SRP).
"""
from uuid import UUID

import structlog
from backend.app.domain.usage.repository import UsageRepository
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class UsageService:
    """
    Application service for usage statistics and evaluation log use cases.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = UsageRepository(session)

    async def get_summary(self, user_id: UUID):
        return await self._repo.get_summary(user_id)

    async def get_evaluations(
        self, conversation_id: UUID | None = None, limit: int = 50
    ):
        return await self._repo.get_evaluations(
            conversation_id=conversation_id, limit=limit
        )
