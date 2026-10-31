"""
Repository for Conversation Domain operations.
"""
from datetime import UTC, datetime
from uuid import UUID

from backend.app.domain.artifact.models import Artifact, ArtifactVersion
from backend.app.domain.base_repository import BaseRepository
from backend.app.domain.conversation.models import (
    Conversation,
    ConversationBranch,
    Message,
    MessageAttachment,
)
from backend.app.domain.file.models import File, FileChunk, FileMetadata
from backend.app.domain.plan.models import Plan
from backend.app.domain.run.models import AgentRun, RunEvent
from backend.app.domain.share.models import ConversationShare
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

    async def get_message_by_id(
        self, message_id: UUID, conversation_id: UUID | None = None, user_id: UUID | None = None
    ) -> Message | None:
        """
        Resolves a message, optionally verifying it belongs to a conversation
        owned by ``user_id`` (IDOR guard for cross-tenant feedback writes).
        """
        statement = select(Message).where(Message.id == message_id)
        if conversation_id:
            statement = statement.where(Message.conversation_id == conversation_id)
        if user_id:
            statement = statement.join(
                Conversation, Conversation.id == Message.conversation_id
            ).where(Conversation.user_id == user_id)
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

    async def search_user_conversations(
        self, user_id: UUID, query: str, limit: int = 20
    ) -> list[Conversation]:
        """
        Full-text-ish search over the user's own conversations: matches the
        title OR any message body (ILIKE). Owner-scoped, so cross-user data is
        never exposed (search is a read of the same owner-filtered set).
        """
        pattern = f"%{query.strip()}%"
        statement = (
            select(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.is_archived == False,  # noqa: E712
            )
            .where(
                Conversation.title.ilike(pattern)
                | Conversation.id.in_(
                    select(Message.conversation_id).where(Message.content.ilike(pattern))
                )
            )
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
        result = await self.session.exec(statement)
        return list(result.all())

    async def create(self, user_id: UUID, title: str = "New Chat", model: str = "complex_reasoning", system_prompt: str | None = None) -> Conversation:
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
        """
        Apply a partial update.

        ``update_data`` is built with ``ConversationUpdate.model_dump(exclude_unset=True)``,
        so a key is present only when the client actually sent it. An explicit
        ``null`` is therefore a request to *clear* a nullable column -- e.g.
        removing a conversation from a project (``project_id: null``).

        The previous rule skipped every ``None``, which silently dropped those
        requests: the PATCH still returned 200 (with the old value), so the
        Projects view's "remove from project" action was a no-op end to end.
        An explicit null for a non-nullable column is ignored rather than issued
        as an UPDATE that would violate its NOT NULL constraint.
        """
        columns = type(conversation).__table__.columns
        for key, value in update_data.items():
            if key not in columns:
                continue
            if value is None and not columns[key].nullable:
                continue
            setattr(conversation, key, value)
        conversation.updated_at = datetime.now(UTC).replace(tzinfo=None)
        self.session.add(conversation)
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def delete(self, conversation: Conversation, *, commit: bool = True) -> None:
        """
        Hard-deletes a conversation and everything that references it in
        dependency order (live FKs are NO ACTION — no cascades exist).

        Each child group is removed with a single bulk DELETE statement; a
        single-statement delete in Postgres is self-consistent for the
        self-referencing ``messages.parent_message_id`` fine.

        ``commit=False`` leaves the transaction open for a caller deleting
        several conversations that needs the whole erasure to be atomic (GDPR
        account erasure passes it). This method used to commit unconditionally,
        so an FK failure part-way through account deletion left half the user's
        data gone and the account still alive.
        """
        conv_id = conversation.id
        message_sub = select(Message.id).where(Message.conversation_id == conv_id)

        # Shares reference conversations.id via a NO-ACTION FK and are not
        # reachable through messages, so they MUST be removed here. They used to
        # be omitted, and deleting a shared conversation raised
        # ``conversation_shares_conversation_id_fkey`` -- which is exactly how
        # GDPR account erasure 500'd on every account that had ever shared a
        # chat (the row was only cleared later, by ``created_by``).
        await self.session.exec(
            delete(ConversationShare).where(ConversationShare.conversation_id == conv_id)
        )

        # Branch links pointing at this conversation (either direction).
        await self.session.exec(
            delete(ConversationBranch).where(
                (ConversationBranch.conversation_id == conv_id)
                | (ConversationBranch.parent_conversation_id == conv_id)
            )
        )

        # Token-level children of the conversation's messages (FK → messages.id).
        await self.session.exec(
            delete(MessageAttachment)
            .where(MessageAttachment.message_id.in_(message_sub))
        )

        # ToolCall rows reference messages.id via a NO-ACTION FK — they MUST be
        # removed before messages or Postgres raises an IntegrityError and the
        # whole deletion rolls back.
        await self.session.exec(
            delete(ToolCall).where(ToolCall.conversation_id == conv_id)
        )

        # Artifacts reference messages.id / conversations.id — versions first
        # (FK → artifacts.id), then the artifacts themselves, then plans.
        artifact_sub = select(Artifact.id).where(
            (Artifact.conversation_id == conv_id)
            | (Artifact.message_id.in_(message_sub))
        )
        await self.session.exec(
            delete(ArtifactVersion).where(ArtifactVersion.artifact_id.in_(artifact_sub))
        )
        await self.session.exec(delete(Artifact).where(Artifact.id.in_(artifact_sub)))
        await self.session.exec(delete(Plan).where(Plan.conversation_id == conv_id))

        # Messages of the conversation (self-referential bulk delete).
        await self.session.exec(
            delete(Message).where(Message.conversation_id == conv_id)
        )

        # Usage telemetry keyed to the conversation.
        await self.session.exec(
            delete(UsageLog).where(UsageLog.conversation_id == conv_id)
        )

        # Files attached to the conversation (and their chunks/metadata).
        await self.session.exec(
            delete(FileChunk).where(FileChunk.file_id.in_(select(File.id).where(File.conversation_id == conv_id)))
        )
        await self.session.exec(
            delete(FileMetadata).where(FileMetadata.file_id.in_(select(File.id).where(File.conversation_id == conv_id)))
        )
        await self.session.exec(delete(File).where(File.conversation_id == conv_id))

        # Run log (§9.27): RunEvent → AgentRun. Both carry NO-ACTION FKs back to
        # the conversation (RunEvent via run_id, AgentRun via conversation_id),
        # so they MUST be removed before the conversation row. They used to be
        # omitted, and every delete of a conversation that had been run raised an
        # IntegrityError -- which also aborted account erasure, since GDPR
        # deletion walks conversations through this method.
        run_sub = select(AgentRun.id).where(AgentRun.conversation_id == conv_id)
        await self.session.exec(delete(RunEvent).where(RunEvent.run_id.in_(run_sub)))
        await self.session.exec(delete(AgentRun).where(AgentRun.conversation_id == conv_id))

        await self.session.delete(conversation)
        if commit:
            await self.session.commit()

    # Message Operations
    async def get_messages(
        self, conversation_id: UUID, limit: int = 100, *, newest: bool = True
    ) -> list[Message]:
        """Return up to ``limit`` messages of a conversation, in chronological order.

        ``newest=True`` (the default) selects the **most recent** ``limit`` rows
        and then returns them ascending. It used to order ascending and limit,
        which silently returned the *oldest* 100 messages -- and every caller
        wants the tail:

        * ``GET /conversations/{id}`` renders the transcript, so a 120-message
          conversation showed the first 100 and omitted the newest 20. That is
          the shape that made rejoin look broken: the recovered answer arrived
          with no user turn anywhere near it, because the turn that produced it
          was one of the rows the limit had discarded.
        * ``messages.py`` and ``plan_service.py`` build LLM context, where the
          most recent turns matter and the oldest are the least useful.

        ``newest=False`` restores the previous window (the earliest ``limit``
        rows). Nothing in ``app/`` asks for it; it exists so the semantics are
        nameable rather than implicit, and because ``_branch_conversation``
        copying the *opening* of a long conversation is a real question whose
        answer should not be a side effect of how the transcript is fetched.

        The window is a subquery of ids rather than a descending fetch reversed
        in Python: one round trip, and no dependence on the caller receiving a
        list in the same order the query returned it. ``id`` is the tiebreaker on
        both sides because ``created_at`` is a naive-UTC timestamp and two
        messages written in the same batch can share one -- without it the
        window and the final ordering can disagree about which rows are newest.
        """
        ascending = (Message.created_at.asc(), Message.id.asc())  # type: ignore[attr-defined]
        if newest:
            window = (
                select(Message.id)
                .where(Message.conversation_id == conversation_id)
                # The only `.desc()` in this method; it is what selects the *tail*
                # of the conversation rather than its opening.
                .order_by(Message.created_at.desc(), Message.id.desc())  # type: ignore[attr-defined]
                .limit(limit)
            )
            statement = (
                select(Message).where(Message.id.in_(window)).order_by(*ascending)
            )
        else:
            statement = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(*ascending)
                .limit(limit)
            )
        result = await self.session.exec(statement)
        return list(result.all())

    async def get_last_message(self, conversation_id: UUID) -> Message | None:
        """The newest row in a conversation, or ``None`` when it is empty.

        Needed by the resend path: reusing a stored user turn is only sound when
        that turn is still the *last* thing in the conversation. A client that
        sends a stale id after another turn landed must be refused rather than
        allowed to rewrite history in the middle of a thread.

        Delegates to ``get_messages`` rather than hand-rolling an ``ORDER BY``:
        the newest-window query already exists there, and two orderings for the
        same question is two places to drift.
        """
        rows = await self.get_messages(conversation_id, limit=1)
        return rows[-1] if rows else None

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

        # Update conversation token_count and updated_at (RMW with row lock)
        from sqlalchemy import update
        await self.session.exec(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(
                token_count=Conversation.token_count + (prompt_tokens + completion_tokens),
                updated_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )

        await self.session.commit()
        await self.session.refresh(message)
        return message

    async def record_feedback(
        self,
        message_id: UUID,
        feedback: str,
        note: str | None = None,
        conversation_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> Message | None:
        """Records feedback, scoped to the owning conversation/user (IDOR guard)."""
        message = await self.get_message_by_id(
            message_id, conversation_id=conversation_id, user_id=user_id
        )
        if message:
            message.user_feedback = feedback
            message.feedback_note = note
            self.session.add(message)
            await self.session.commit()
            await self.session.refresh(message)
        return message

    async def delete_message(
        self,
        message_id: UUID,
        conversation_id: UUID,
        user_id: UUID | None = None,
        *,
        cascade: bool = True,
        commit: bool = True,
    ) -> int:
        """Delete one message and — by default — everything that came after it.

        ## Why the cascade, and why it is keyed on order and not on the chain

        Messages are appended in order, so "everything after this row in
        ``created_at`` order" is exactly "the turns this message produced".
        Deleting the last assistant reply leaves the user turn intact; deleting
        the user turn must also take its reply, or the transcript is left with an
        orphaned answer to a question no longer on screen — which reads as the
        app having lost the question, not as the user having removed a turn.

        The cascade deliberately does **not** walk ``parent_message_id``: that
        column is nullable, is ``None`` on the vast majority of rows (the chat
        path only sets it when the client sends one), and a chain walk would
        therefore silently drop rows that are unambiguously part of the same
        run. ``created_at`` is the ordering the transcript already renders in.

        Returns the number of rows deleted. Scoped to ``conversation_id`` (and
        ``user_id`` when given) so a message id from another conversation cannot
        truncate this one — the IDOR guard mirrors ``record_feedback``.
        """
        # Resolve the target first, under the same ownership scope, so a foreign
        # id is a 404 rather than a silent zero-row delete.
        target = await self.get_message_by_id(
            message_id, conversation_id=conversation_id, user_id=user_id
        )
        if target is None:
            return 0

        victim_ids: list[UUID] = []
        if cascade:
            # Tie-break on id so two rows sharing a ``created_at`` (a user turn
            # and its reply written in the same millisecond is realistic) cannot
            # be split by the comparison, which would delete one and keep the
            # other.
            victims_stmt = select(Message).where(
                Message.conversation_id == conversation_id,
                (Message.created_at > target.created_at)
                | (
                    (Message.created_at == target.created_at)
                    & (Message.id != target.id)
                ),
            )
            victims = (await self.session.exec(victims_stmt)).all()
            victim_ids = [row.id for row in victims]

        await self._purge_message_rows([target.id, *victim_ids])
        if commit:
            await self.session.commit()
        return len(victim_ids) + 1 if cascade else 1

    async def _purge_message_rows(self, message_ids: list[UUID]) -> None:
        """Delete message rows and everything that references them, in FK order.

        Every FK into ``messages`` is NO ACTION -- there is no cascade at the
        database level -- and ``messages.parent_message_id`` is
        self-referencing. The row-wise ORM deletes this used to issue therefore
        500'd on the real database: SQLAlchemy flushes a batch of ORM deletes as
        one ``executemany`` in arbitrary order, so a parent went before its
        linked child and Postgres raised ``ForeignKeyViolation`` on
        ``messages_parent_message_id_fkey``. The unit tests could not see it
        because ``FakeSession`` enforces no constraints -- the Delete button was
        verified live and failed with a 500 before this fix.

        Two properties make the bulk form safe, and both are pinned in
        ``test_message_edit_delete.py``:

        * children (attachments, tool calls, branch links, artifacts +
          versions) are removed by their own statements **before** the messages;
        * the messages themselves go in **one** ``DELETE ... WHERE id IN
          (...)``. A single statement is self-consistent for a self-referencing
          FK -- the constraint is checked when the statement completes, at
          which point parent and child are both gone. This mirrors
          ``delete(conversation)``, which already relied on the same property.
        """
        if not message_ids:
            return

        await self.session.exec(
            delete(MessageAttachment).where(MessageAttachment.message_id.in_(message_ids))
        )
        await self.session.exec(
            delete(ToolCall).where(ToolCall.message_id.in_(message_ids))
        )
        # Branch links point at a fork message; the forked conversation itself
        # survives, only the link row references the deleted message.
        await self.session.exec(
            delete(ConversationBranch).where(
                ConversationBranch.fork_message_id.in_(message_ids)
            )
        )
        artifact_sub = select(Artifact.id).where(Artifact.message_id.in_(message_ids))
        await self.session.exec(
            delete(ArtifactVersion).where(ArtifactVersion.artifact_id.in_(artifact_sub))
        )
        await self.session.exec(
            delete(Artifact).where(Artifact.message_id.in_(message_ids))
        )
        await self.session.exec(
            delete(Message).where(Message.id.in_(message_ids))
        )

    async def update_message_content(
        self,
        message_id: UUID,
        content: str,
        conversation_id: UUID,
        user_id: UUID | None = None,
        *,
        truncate_after: bool = True,
        commit: bool = True,
    ) -> Message | None:
        """Rewrite a message's content, and drop every turn that followed it.

        Edit-and-resend is a *time-travel* edit: the user is saying "this is what
        I meant", which invalidates the answer the old text produced and every
        turn after it. Keeping them would leave the transcript claiming the model
        answered the new question when it answered the old one. Truncation is
        therefore the default and is the reason this is a PATCH on a *user*
        message rather than a general content editor.

        Only ``content`` is written; the row's id, ordering and parent link are
        untouched, so the message keeps its place in the transcript.
        """
        message = await self.get_message_by_id(
            message_id, conversation_id=conversation_id, user_id=user_id
        )
        if message is None:
            return None

        # ``messages`` has no ``updated_at`` column (only ``created_at``), and
        # adding one would need a migration for a field nothing renders. The edit
        # is therefore content-only, which also keeps the row's ordering — the
        # cascade below compares ``created_at``, and bumping it would move the
        # edited turn to the end of the transcript.
        message.content = content
        self.session.add(message)

        if truncate_after:
            victims_stmt = select(Message).where(
                Message.conversation_id == conversation_id,
                (Message.created_at > message.created_at)
                | (
                    (Message.created_at == message.created_at)
                    & (Message.id != message.id)
                ),
            )
            victims = (await self.session.exec(victims_stmt)).all()
            # The same FK-ordering rules as ``delete_message`` apply here: a
            # discarded answer routinely owns tool_calls/attachment rows, and
            # the row-wise form hit the identical ``ForeignKeyViolation`` on
            # the live database (the edit path merely had no children in the
            # one conversation exercised before the fix).
            await self._purge_message_rows([row.id for row in victims])

        if commit:
            await self.session.commit()
            await self.session.refresh(message)
        return message

    # Branching / Forking
    async def fork_conversation(
        self,
        user_id: UUID,
        parent_conv_id: UUID,
        fork_message_id: UUID | None,
        branch_name: str,
    ) -> Conversation:
        # IDOR guard: scope the parent lookup to the requesting user so a stranger's
        # conversation can never be forked/read.
        parent_conv = await self.get_by_id(parent_conv_id, user_id=user_id)
        if not parent_conv:
            raise ValueError("Parent conversation not found")

        # Read the parent's messages first: the fork point has to be one of them,
        # and even an omitted one still needs a real message id for the branch
        # link (`conversation_branches.fork_message_id` is NOT NULL with an FK to
        # `messages.id`).
        parent_messages = await self.get_messages(parent_conv_id)
        if fork_message_id is None:
            # "Fork from here" with no id named: the latest turn is the only fork
            # point the UI offers, so default to the end. An empty conversation
            # has nothing to branch, and the FK forbids inventing an id for it.
            if not parent_messages:
                raise ValueError("Cannot fork an empty conversation")
            fork_message_id = parent_messages[-1].id
        elif all(msg.id != fork_message_id for msg in parent_messages):
            # A fork point from another conversation would copy every message and
            # then fail the branch-link FK; reject it explicitly instead.
            raise ValueError("Fork message does not belong to the conversation")

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
