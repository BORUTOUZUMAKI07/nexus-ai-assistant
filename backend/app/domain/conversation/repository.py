"""
Repository for Conversation Domain operations.
"""
from datetime import datetime
from uuid import UUID

from backend.app.domain.base_repository import BaseRepository
from backend.app.domain.conversation.models import (
    Conversation,
    ConversationBranch,
    Message,
    MessageAttachment,
)
from backend.app.domain.file.models import File, FileChunk, FileMetadata
from backend.app.domain.tool.models import ToolCall
from backend.app.domain.usage.models import UsageLog
from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession


class ConversationRepository(BaseRepository[Conversation]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Conversation)

    async def get_by_id(self, conversation_id: UUID, user_id: UUID | None = None) -> Conversation | None:
        statement = select(Conversation).where(Conversation.id == conversation_id)
        if user_id:
            statement = statement.where(Conversation.user_id == user_id)
        result = await self.session.exec(statement)
        return result.first()

    async def get_all_by_user(
        self,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
        archived: bool = False,
    ) -> list[Conversation]:
        statement = (
            select(Conversation)
            .where(Conversation.user_id == user_id, Conversation.is_archived == archived)
            .order_by(Conversation.is_pinned.desc(), Conversation.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.exec(statement)
        return list(result.all())

    async def create(self, user_id: UUID, title: str = "New Chat", model: str = "llama-3.3-70b-versatile", system_prompt: str | None = None) -> Conversation:
        conversation = Conversation(
            user_id=user_id,
            title=title,
            model=model,
            system_prompt=system_prompt,
        )
        self.session.add(conversation)
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def update(self, conversation: Conversation, update_data: dict) -> Conversation:
        for key, value in update_data.items():
            if value is not None and hasattr(conversation, key):
                setattr(conversation, key, value)
        conversation.updated_at = datetime.utcnow()
        self.session.add(conversation)
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def delete(self, conversation: Conversation) -> None:
        """
        Hard-deletes a conversation and everything that references it in
        dependency order (live FKs are NO ACTION — no cascades exist).

        Each child group is removed with a single bulk DELETE statement; a
        single-statement delete in Postgres is self-consistent for the
        self-referencing ``messages.parent_message_id`` fine.
        """
        conv_id = conversation.id
        message_sub = select(Message.id).where(Message.conversation_id == conv_id)

        # Branch links pointing at this conversation (either direction).
        await self.session.exec(
            delete(ConversationBranch).where(
                (ConversationBranch.conversation_id == conv_id)
                | (ConversationBranch.parent_conversation_id == conv_id)
            )
        )

        # Token-level children of the conversation's messages.
        await self.session.exec(
            delete(MessageAttachment)
            .where(MessageAttachment.message_id.in_(message_sub))
        )

        # Messages of the conversation (self-referential bulk delete).
        await self.session.exec(
            delete(Message).where(Message.conversation_id == conv_id)
        )

        # Usage/tool telemetry keyed to the conversation or its messages.
        await self.session.exec(
            delete(UsageLog).where(UsageLog.conversation_id == conv_id)
        )
        await self.session.exec(
            delete(ToolCall).where(ToolCall.conversation_id == conv_id)
        )

        # Files attached to the conversation (and their chunks/metadata).
        await self.session.exec(
            delete(FileChunk).where(FileChunk.file_id.in_(select(File.id).where(File.conversation_id == conv_id)))
        )
        await self.session.exec(
            delete(FileMetadata).where(FileMetadata.file_id.in_(select(File.id).where(File.conversation_id == conv_id)))
        )
        await self.session.exec(delete(File).where(File.conversation_id == conv_id))

        await self.session.delete(conversation)
        await self.session.commit()

    # Message Operations
    async def get_messages(self, conversation_id: UUID, limit: int = 100) -> list[Message]:
        statement = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
            .limit(limit)
        )
        result = await self.session.exec(statement)
        return list(result.all())

    async def get_message_by_id(self, message_id: UUID) -> Message | None:
        statement = select(Message).where(Message.id == message_id)
        result = await self.session.exec(statement)
        return result.first()

    async def add_message(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
        parent_message_id: UUID | None = None,
        thought_process: str | None = None,
        model: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        citations: list[dict] | None = None,
        tool_calls: list[dict] | None = None,
        metadata_json: dict | None = None,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            parent_message_id=parent_message_id,
            role=role,
            content=content,
            thought_process=thought_process,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            citations=citations or [],
            tool_calls=tool_calls or [],
            metadata_json=metadata_json or {},
        )
        self.session.add(message)

        # Update conversation token_count and updated_at
        conversation = await self.get_by_id(conversation_id)
        if conversation:
            conversation.token_count += (prompt_tokens + completion_tokens)
            conversation.updated_at = datetime.utcnow()
            self.session.add(conversation)

        await self.session.commit()
        await self.session.refresh(message)
        return message

    async def record_feedback(self, message_id: UUID, feedback: str, note: str | None = None) -> Message | None:
        message = await self.get_message_by_id(message_id)
        if message:
            message.user_feedback = feedback
            message.feedback_note = note
            self.session.add(message)
            await self.session.commit()
            await self.session.refresh(message)
        return message

    # Branching / Forking
    async def fork_conversation(self, user_id: UUID, parent_conv_id: UUID, fork_message_id: UUID, branch_name: str) -> Conversation:
        parent_conv = await self.get_by_id(parent_conv_id)
        if not parent_conv:
            raise ValueError("Parent conversation not found")

        # Create new conversation
        new_conv = Conversation(
            user_id=user_id,
            title=f"{parent_conv.title} (Branch: {branch_name})",
            model=parent_conv.model,
            system_prompt=parent_conv.system_prompt,
        )
        self.session.add(new_conv)
        await self.session.commit()
        await self.session.refresh(new_conv)

        # Record branch link
        branch = ConversationBranch(
            conversation_id=new_conv.id,
            parent_conversation_id=parent_conv_id,
            fork_message_id=fork_message_id,
            branch_name=branch_name,
        )
        self.session.add(branch)

        # Copy messages up to fork_message_id
        parent_messages = await self.get_messages(parent_conv_id)
        msg_map = {}
        for msg in parent_messages:
            new_msg = Message(
                conversation_id=new_conv.id,
                parent_message_id=msg_map.get(msg.parent_message_id),
                role=msg.role,
                content=msg.content,
                thought_process=msg.thought_process,
                model=msg.model,
                prompt_tokens=msg.prompt_tokens,
                completion_tokens=msg.completion_tokens,
                total_tokens=msg.total_tokens,
                citations=msg.citations,
                tool_calls=msg.tool_calls,
                metadata_json=msg.metadata_json,
            )
            self.session.add(new_msg)
            await self.session.flush()
            msg_map[msg.id] = new_msg.id
            if msg.id == fork_message_id:
                break

        await self.session.commit()
        return new_conv
