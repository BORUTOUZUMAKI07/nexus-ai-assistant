"""
Conversation Application Service.
Owns all conversation CRUD, forking, and message retrieval use cases (SRP).
"""
from typing import Any
from uuid import UUID

import structlog
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.conversation.models import Conversation, Message
from backend.app.domain.conversation.repository import ConversationRepository
from backend.app.domain.conversation.schemas import (
    BranchCreate,
    ConversationCreate,
    ConversationDetailResponse,
)
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class ConversationService:
    """
    Application service for conversation management use cases.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = ConversationRepository(session)

    async def list_conversations(
        self,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
        archived: bool = False,
    ) -> list[Conversation]:
        return await self._repo.get_all_by_user(
            user_id=user_id, limit=limit, offset=offset, archived=archived
        )

    async def create_conversation(
        self, user_id: UUID, conv_in: ConversationCreate
    ) -> Conversation:
        conv = await self._repo.create(
            user_id=user_id,
            title=conv_in.title or "New Chat",
            model=conv_in.model or "llama-3.3-70b-versatile",
            system_prompt=conv_in.system_prompt,
        )
        logger.info("conversation_created", conversation_id=str(conv.id))
        return conv

    async def get_conversation(
        self, conversation_id: UUID, user_id: UUID
    ) -> ConversationDetailResponse:
        conv = await self._repo.get_by_id(conversation_id, user_id=user_id)
        if not conv:
            raise ResourceNotFoundError("Conversation", str(conversation_id))
        messages = await self._repo.get_messages(conversation_id)
        detail = ConversationDetailResponse.model_validate(conv)
        detail.messages = messages
        return detail

    async def update_conversation(
        self,
        conversation_id: UUID,
        user_id: UUID,
        update_data: dict[str, Any],
    ) -> Conversation:
        conv = await self._repo.get_by_id(conversation_id, user_id=user_id)
        if not conv:
            raise ResourceNotFoundError("Conversation", str(conversation_id))
        return await self._repo.update(conv, update_data)

    async def delete_conversation(
        self, conversation_id: UUID, user_id: UUID
    ) -> None:
        conv = await self._repo.get_by_id(conversation_id, user_id=user_id)
        if not conv:
            raise ResourceNotFoundError("Conversation", str(conversation_id))
        await self._repo.delete(conv)
        logger.info("conversation_deleted", conversation_id=str(conversation_id))

    async def fork_conversation(
        self,
        conversation_id: UUID,
        user_id: UUID,
        branch_in: BranchCreate,
    ) -> Conversation:
        try:
            return await self._repo.fork_conversation(
                user_id=user_id,
                parent_conv_id=conversation_id,
                fork_message_id=branch_in.fork_message_id,
                branch_name=branch_in.branch_name,
            )
        except ValueError as exc:
            raise ResourceNotFoundError("Conversation", str(conversation_id)) from exc

    async def add_message(self, **kwargs) -> Message:
        return await self._repo.add_message(**kwargs)

    async def get_messages(self, conversation_id: UUID) -> list[Message]:
        return await self._repo.get_messages(conversation_id)

    async def record_feedback(
        self, message_id: UUID, feedback: str, note: str | None = None
    ) -> Message:
        msg = await self._repo.record_feedback(
            message_id, feedback=feedback, note=note
        )
        if not msg:
            raise ResourceNotFoundError("Message", str(message_id))
        return msg
