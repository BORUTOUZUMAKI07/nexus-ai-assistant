"""
Read-only conversation sharing service.

Owner mints an unguessable token; anyone with the token can read the
conversation without an account. Shares are revocable and optionally expire.
"""
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from backend.app.core.config import settings
from backend.app.domain.conversation.models import Conversation, Message
from backend.app.domain.conversation.repository import ConversationRepository
from backend.app.domain.share.models import ConversationShare
from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class ShareService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, user_id: UUID, conversation_id: UUID, ttl_seconds: int | None = None
    ) -> ConversationShare:
        conv = await self.session.get(Conversation, conversation_id)
        if not conv or conv.user_id != user_id:
            raise ValueError("Conversation not found")
        token = secrets.token_urlsafe(24)
        expires_at = None
        if ttl_seconds:
            expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=ttl_seconds)
        share = ConversationShare(
            conversation_id=conversation_id,
            created_by=user_id,
            token=token,
            expires_at=expires_at,
        )
        self.session.add(share)
        await self.session.commit()
        await self.session.refresh(share)
        logger.info("conversation_share_created", conversation_id=str(conversation_id), user_id=str(user_id))
        return share

    async def get_share(self, token: str) -> ConversationShare | None:
        res = await self.session.exec(
            select(ConversationShare).where(
                ConversationShare.token == token,
                ConversationShare.is_active == True,  # noqa: E712
            )
        )
        share = res.first()
        if not share:
            return None
        if share.expires_at and share.expires_at < datetime.now(UTC).replace(tzinfo=None):
            return None
        return share

    async def read_public(self, token: str) -> dict | None:
        """Public read: conversation summary + messages (no owner data, no tool internals)."""
        share = await self.get_share(token)
        if not share:
            return None
        conv = await self.session.get(Conversation, share.conversation_id)
        if not conv:
            return None
        messages = (await self.session.exec(
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.asc())
        )).all()
        return {
            "conversation": {
                "id": str(conv.id),
                "title": conv.title,
                "created_at": conv.created_at.isoformat() if conv.created_at else None,
            },
            "messages": [
                {"role": m.role, "content": m.content, "model": m.model,
                 "citations": m.citations or [],
                 "created_at": m.created_at.isoformat() if m.created_at else None}
                for m in messages
            ],
        }

    async def list_for_conversation(self, user_id: UUID, conversation_id: UUID) -> list[ConversationShare]:
        conv = await self.session.get(Conversation, conversation_id)
        if not conv or conv.user_id != user_id:
            return []
        res = await self.session.exec(
            select(ConversationShare).where(ConversationShare.conversation_id == conversation_id)
        )
        return list(res.all())

    async def revoke(self, user_id: UUID, conversation_id: UUID) -> bool:
        """Deactivate all shares for a conversation the user owns."""
        conv = await self.session.get(Conversation, conversation_id)
        if not conv or conv.user_id != user_id:
            return False
        await self.session.exec(
            delete(ConversationShare).where(
                ConversationShare.conversation_id == conversation_id,
                ConversationShare.created_by == user_id,
            )
        )
        await self.session.commit()
        return True