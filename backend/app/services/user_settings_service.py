"""
User Settings Application Service.
Owns user profile settings and memory management use cases (SRP).
"""
from uuid import UUID

import structlog
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.user.repository import UserRepository
from backend.app.domain.user.schemas import (
    UserMemoryCreate,
    UserSettingsUpdate,
)
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class UserSettingsService:
    """
    Application service for user settings, memory, and BYOK API key management.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = UserRepository(session)

    async def get_settings(self, user_id: UUID):
        settings = await self._repo.get_settings(user_id)
        if not settings:
            settings = await self._repo.upsert_settings(user_id, {})
        return settings

    async def update_settings(self, user_id: UUID, settings_in: UserSettingsUpdate):
        return await self._repo.upsert_settings(
            user_id, settings_in.model_dump(exclude_unset=True)
        )

    async def list_memories(self, user_id: UUID):
        return await self._repo.get_memories(user_id)

    async def add_memory(self, user_id: UUID, mem_in: UserMemoryCreate):
        return await self._repo.create_memory(
            user_id=user_id,
            content=mem_in.content,
            category=mem_in.category,
            confidence=mem_in.confidence,
            source_conv_id=mem_in.source_conversation_id,
        )

    async def delete_memory(self, user_id: UUID, memory_id: UUID) -> None:
        success = await self._repo.delete_memory(user_id, memory_id)
        if not success:
            raise ResourceNotFoundError("Memory", str(memory_id))

    async def list_api_keys(self, user_id: UUID):
        return await self._repo.get_api_keys(user_id)

    async def save_api_key(self, user_id: UUID, provider: str, raw_key: str, label: str):
        return await self._repo.save_api_key(
            user_id=user_id,
            provider=provider,
            raw_key=raw_key,
            label=label,
        )
