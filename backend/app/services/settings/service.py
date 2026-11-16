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

    async def list_memories(self, user_id: UUID, *, limit: int = 50, offset: int = 0):
        # Opportunistic decay check (piggybacked on reads, $0 cost, no cron daemon)
        try:
            await self._repo.decay_stale_memories(batch_limit=100)
        except Exception as exc:
            logger.warning("opportunistic_memory_decay_skipped", error=str(exc))

        return await self._repo.get_memories(user_id, limit=limit, offset=offset)

    async def add_memory(self, user_id: UUID, mem_in: UserMemoryCreate):
        mem0_id: str | None = None
        try:
            from backend.app.services.memory.long_term import long_term_memory

            mem0_id = await long_term_memory.add(
                content=mem_in.content,
                user_id=user_id,
                metadata={"category": mem_in.category or "preference"},
            )
        except Exception as exc:
            logger.warning("mem0_add_failed_during_manual_create", error=str(exc))

        return await self._repo.create_memory(
            user_id=user_id,
            content=mem_in.content,
            category=mem_in.category,
            confidence=mem_in.confidence,
            source_conv_id=mem_in.source_conversation_id,
            mem0_id=mem0_id,
        )

    async def delete_memory(self, user_id: UUID, memory_id: UUID) -> None:
        memory = await self._repo.get_memory_by_id(user_id, memory_id)
        if not memory:
            raise ResourceNotFoundError("Memory", str(memory_id))

        mem0_id = memory.mem0_id
        success = await self._repo.delete_memory(user_id, memory_id)
        if not success:
            raise ResourceNotFoundError("Memory", str(memory_id))

        if mem0_id:
            try:
                from backend.app.services.memory.long_term import long_term_memory

                await long_term_memory.delete(mem0_id)
            except Exception as exc:
                logger.warning("mem0_delete_failed", mem0_id=mem0_id, error=str(exc))

    async def list_api_keys(self, user_id: UUID, *, limit: int = 50, offset: int = 0):
        return await self._repo.get_api_keys(user_id, limit=limit, offset=offset)

    async def save_api_key(self, user_id: UUID, provider: str, raw_key: str, label: str):
        return await self._repo.save_api_key(
            user_id=user_id,
            provider=provider,
            raw_key=raw_key,
            label=label,
        )

    async def delete_api_key(self, user_id: UUID, key_id: UUID) -> None:
        success = await self._repo.delete_api_key(user_id, key_id)
        if not success:
            raise ResourceNotFoundError("APIKey", str(key_id))
