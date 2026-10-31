"""
Conversation Application Service.
Owns all conversation CRUD, forking, and message retrieval use cases (SRP).
"""
from typing import Any, NamedTuple
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
from backend.app.domain.file.models import File
from backend.app.infrastructure.vector.qdrant_client import vector_db
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class RegenerationTarget(NamedTuple):
    """What ``prepare_regeneration`` hands back to its route.

    ``user_message_id`` is the identity of the prompt row the client must reuse
    when it resends. Without it the resend looks like a brand-new turn and the
    stream route appends a second copy of the question -- a duplicate row with a
    different id that no test comparing *text* can distinguish.
    """

    prompt: str
    user_message_id: UUID


class ConversationService:
    """
    Application service for conversation management use cases.
    """

    def __init__(self, session: AsyncSession, vector_store=None) -> None:
        self._repo = ConversationRepository(session)
        self._session = session
        # Vector purge on conversation delete is injected for fake-based tests.
        self._vector_store = vector_store if vector_store is not None else vector_db

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

    async def search_conversations(self, user_id: UUID, query: str, limit: int = 20) -> list[Conversation]:
        """Owner-scoped search across the user's conversation titles + message bodies."""
        return await self._repo.search_user_conversations(user_id=user_id, query=query, limit=limit)

    async def create_conversation(
        self, user_id: UUID, conv_in: ConversationCreate
    ) -> Conversation:
        conv = await self._repo.create(
            user_id=user_id,
            title=conv_in.title or "New Chat",
            model=conv_in.model or "complex_reasoning",
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

        # Cross-store cleanup (HLD: saga/reconciliation): snapshot the
        # conversation's files before the cascade so their RAG vectors can be
        # purged after the rows are gone — otherwise deleting a conversation
        # leaks its indexed content into future retrievals.
        file_ids = [
            f.id
            for f in (await self._session.exec(
                select(File).where(File.conversation_id == conversation_id)
            )).all()
        ]

        await self._repo.delete(conv)

        for fid in file_ids:
            try:
                await self._vector_store.delete_by_filter({"file_id": str(fid)})
            except Exception as exc:
                logger.warning(
                    "conversation_vector_purge_failed",
                    conversation_id=str(conversation_id),
                    file_id=str(fid),
                    error=str(exc),
                )
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
        self, message_id: UUID, feedback: str, note: str | None = None,
        conversation_id: UUID | None = None, user_id: UUID | None = None,
    ) -> Message:
        msg = await self._repo.record_feedback(
            message_id, feedback=feedback, note=note,
            conversation_id=conversation_id, user_id=user_id,
        )
        if not msg:
            raise ResourceNotFoundError("Message", str(message_id))
        # ── Bandit reward stream (fail-open): feedback on an experimental
        # variant feeds the ε-greedy reward counts so serve-time selection
        # learns from real outcomes.
        await self._record_bandit_reward(msg, feedback=feedback, user_id=user_id)
        return msg

    async def delete_message(
        self,
        message_id: UUID,
        conversation_id: UUID,
        user_id: UUID | None = None,
    ) -> int:
        """Delete a turn (and, by default, everything after it). See the repo."""
        deleted = await self._repo.delete_message(
            message_id, conversation_id, user_id=user_id
        )
        if deleted == 0:
            raise ResourceNotFoundError("Message", str(message_id))
        logger.info(
            "message_deleted",
            conversation_id=str(conversation_id),
            message_id=str(message_id),
            deleted=deleted,
        )
        return deleted

    async def edit_message(
        self,
        message_id: UUID,
        content: str,
        conversation_id: UUID,
        user_id: UUID | None = None,
    ) -> Message:
        """Rewrite a user turn and drop the turns it invalidated (time-travel edit)."""
        msg = await self._repo.update_message_content(
            message_id, content, conversation_id, user_id=user_id
        )
        if msg is None:
            raise ResourceNotFoundError("Message", str(message_id))
        logger.info(
            "message_edited",
            conversation_id=str(conversation_id),
            message_id=str(message_id),
        )
        return msg

    async def reuse_user_turn(
        self,
        conversation_id: UUID,
        content: str,
        reuse_message_id: UUID,
        user_id: UUID | None = None,
    ) -> Message:
        """Adopt an existing user row as the turn a resend is asking again.

        The stream route persists the user turn itself, so every resend -- edit
        and resend, regenerate -- would otherwise store the question a second
        time: the edit/regenerate endpoints leave one row in place, and the
        resend appends another. The duplicate is invisible client-side (the hook
        already truncated its local copy) and indistinguishable from correct
        behaviour if you compare text, which is why the decision is made by
        **identity**: the client sends back the id it edited, or the id the
        regenerate endpoint returned.

        Strict on purpose. A stale id -- one that is no longer the newest row --
        means the client and the server disagree about the transcript, and
        silently appending a duplicate is exactly the defect this exists to
        prevent. The caller turns these into 404/409 rather than swallowing them
        into the stream route's fail-open persistence guard.
        """
        target = await self._repo.get_message_by_id(
            reuse_message_id, conversation_id=conversation_id, user_id=user_id
        )
        if target is None:
            raise ResourceNotFoundError("Message", str(reuse_message_id))
        if target.role != "user":
            raise ValueError("Only a user message can be reused for a resend")

        last = await self._repo.get_last_message(conversation_id)
        if last is None or last.id != target.id:
            raise ValueError("Cannot reuse a message that is not the latest turn")

        if target.content != content:
            updated = await self._repo.update_message_content(
                reuse_message_id,
                content,
                conversation_id,
                user_id=user_id,
                truncate_after=False,
            )
            if updated is not None:
                return updated
        return target

    async def prepare_regeneration(
        self,
        message_id: UUID,
        conversation_id: UUID,
        user_id: UUID | None = None,
    ) -> RegenerationTarget:
        """Drop an assistant reply and return the user prompt that produced it.

        Regenerate is a delete plus a resend, expressed as one server call so the
        client cannot end up with the reply removed and the prompt lost — which
        would leave the user with no way to re-ask. The preceding user turn is
        read *before* the deletion, and the deletion cascades everything after
        it, so the returned text is the last thing standing.

        The prompt's **id** travels with it: the client resends the same turn,
        and the stream route must reuse that row rather than append a second
        copy of the question.
        """
        message = await self._repo.get_message_by_id(
            message_id, conversation_id=conversation_id, user_id=user_id
        )
        if message is None:
            raise ResourceNotFoundError("Message", str(message_id))

        # Only an assistant turn is regenerable: regenerating a user turn would
        # be "ask again", which is just sending a new message.
        if message.role != "assistant":
            raise ValueError("Only an assistant message can be regenerated")

        history = await self._repo.get_messages(conversation_id)
        prompt = ""
        prompt_id: UUID | None = None
        for row in history:
            if row.id == message.id:
                break
            if row.role == "user":
                prompt = row.content
                prompt_id = row.id
        if not prompt or prompt_id is None:
            raise ValueError("No preceding user message to regenerate from")

        # Delete the reply; cascade is harmless here because an assistant reply is
        # terminal within its turn, but it keeps one rule for every caller.
        await self._repo.delete_message(
            message_id, conversation_id, user_id=user_id, cascade=True
        )
        logger.info(
            "message_regenerated",
            conversation_id=str(conversation_id),
            message_id=str(message_id),
            user_message_id=str(prompt_id),
        )
        return RegenerationTarget(prompt=prompt, user_message_id=prompt_id)

    async def _record_bandit_reward(
        self, msg: Message, feedback: str, user_id: UUID | None
    ) -> None:
        try:
            metadata = msg.metadata_json or {}
            variant = metadata.get("prompt_variant")
            if not variant or variant == "default" or feedback not in ("thumbs_up", "thumbs_down"):
                return
            from backend.app.services.bandit_service import bandit_service

            reward = 1.0 if feedback == "thumbs_up" else 0.0
            await bandit_service.record_reward(
                self._session,
                experiment="chat_system_prompt",
                variant=str(variant),
                reward=reward,
                user_id=user_id,
            )
            logger.info(
                "bandit_reward_recorded",
                experiment="chat_system_prompt",
                variant=variant,
                reward=reward,
                message_id=str(msg.id),
            )
        except Exception as exc:
            logger.warning("bandit_reward_recording_skipped", error=str(exc))
