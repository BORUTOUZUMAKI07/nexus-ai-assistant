"""
Per-message edit / delete / regenerate — repository cascade + service rules.

The chat UI's four message actions (delete, edit-and-resend, regenerate, branch)
all hang off two repository methods whose whole job is *ordering*: "a turn and
everything that came after it" is decided by ``created_at``, not by
``parent_message_id``. That choice is load-bearing and invisible from a passing
endpoint test, so it is pinned here directly against the in-memory ``FakeSession``:

  * the cascade keys on ``created_at`` **with an id tiebreaker**, so two rows
    written in the same millisecond cannot be split by the comparison;
  * a foreign / unknown message id is a **404-shaped zero**, not a silent
    cross-conversation truncation (the IDOR guard);
  * an edit is **content-only** — the row keeps its ``created_at`` (the table has
    no ``updated_at``), so it does not jump to the end of the transcript;
  * regenerate is assistant-only and returns the *stored* prompt, read before
    the reply is deleted.
  * deletion is **FK-safe**: every FK into ``messages`` is NO ACTION and
    ``messages.parent_message_id`` is self-referencing, so child rows must be
    purged first and the messages must go in one bulk statement. This is the
    defect found live on 2026-10-24 -- the UI's Delete button 500'd on
    ``messages_parent_message_id_fkey`` for every conversation whose reply
    carried a parent link, and no unit test could see it because the
    FakeSession enforces no constraints.

Run from ``backend/``.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.artifact.models import Artifact, ArtifactVersion
from backend.app.domain.conversation.models import (
    ConversationBranch,
    Message,
    MessageAttachment,
)
from backend.app.domain.conversation.repository import ConversationRepository
from backend.app.domain.tool.models import ToolCall
from backend.app.services.conversations.service import ConversationService
from backend.tests.fakes import FakeSession


def _make_user(user_id: uuid.UUID | None = None) -> object:
    return type(
        "User",
        (),
        {
            "id": user_id or uuid.uuid4(),
            "role": "user",
            "email": "u@x.io",
            "username": "u",
            "is_active": True,
        },
    )()


def _turn(
    conversation_id: uuid.UUID,
    *,
    role: str,
    content: str,
    offset_ms: int,
    base: datetime | None = None,
    message_id: uuid.UUID | None = None,
) -> Message:
    """One message, placed at a deterministic offset so ordering is exact."""
    base = base or datetime(2026, 10, 21, 12, 0, 0)
    return Message(
        id=message_id or uuid.uuid4(),
        conversation_id=conversation_id,
        role=role,
        content=content,
        created_at=base + timedelta(milliseconds=offset_ms),
    )


def _seed_turn(
    fake: FakeSession, user_id: uuid.UUID, texts: list[tuple[str, str]]
) -> tuple[uuid.UUID, list[Message]]:
    """Seed a conversation of alternating user/assistant turns.

    ``texts`` is a list of ``(role, content)`` and is laid out 100ms apart, which
    is coarse enough that only the id tiebreaker — not clock resolution — can
    separate rows sharing a timestamp in the tie test.
    """
    conversation_id = uuid.uuid4()
    rows = [
        _turn(conversation_id, role=role, content=content, offset_ms=i * 100)
        for i, (role, content) in enumerate(texts)
    ]
    fake.seed(Message, rows)
    return conversation_id, rows


# ─────────────────────────────────────────────────────────────────────────────
# Repository: delete cascade
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deleting_a_user_turn_takes_its_reply_and_everything_after():
    fake = FakeSession()
    user_id = uuid.uuid4()
    conversation_id, rows = _seed_turn(
        fake,
        user_id,
        [
            ("user", "first question"),
            ("assistant", "first answer"),
            ("user", "second question"),
            ("assistant", "second answer"),
        ],
    )
    repo = ConversationRepository(fake)

    deleted = await repo.delete_message(rows[0].id, conversation_id)

    # Everything from the first user turn onwards: the orphaned answer to a
    # question that is gone would read as data loss, not as an edit.
    assert deleted == 4
    assert fake.rows[Message] == []


@pytest.mark.asyncio
async def test_deleting_the_last_reply_leaves_the_question_intact():
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake, uuid.uuid4(), [("user", "q"), ("assistant", "a")]
    )
    repo = ConversationRepository(fake)

    deleted = await repo.delete_message(rows[1].id, conversation_id)

    assert deleted == 1
    survivors = {m.id for m in fake.rows[Message]}
    assert survivors == {rows[0].id}


@pytest.mark.asyncio
async def test_cascade_does_not_cross_conversations():
    """A cascade is scoped to its own conversation, even at a shared timestamp."""
    fake = FakeSession()
    other_conv = uuid.uuid4()
    conversation_id, rows = _seed_turn(
        fake, uuid.uuid4(), [("user", "q"), ("assistant", "a")]
    )
    # Same timestamps, different conversation. A cascade keyed only on
    # ``created_at`` would delete these too.
    strangers = [
        _turn(other_conv, role="user", content="theirs", offset_ms=0),
        _turn(other_conv, role="assistant", content="theirs", offset_ms=50),
    ]
    fake.seed(Message, strangers)
    repo = ConversationRepository(fake)

    deleted = await repo.delete_message(rows[0].id, conversation_id)

    assert deleted == 2
    survivors = {m.id for m in fake.rows[Message]}
    assert survivors == {s.id for s in strangers}


@pytest.mark.asyncio
async def test_same_timestamp_rows_are_split_by_the_id_tiebreaker():
    """The comparison alone cannot separate a turn from its same-ms reply.

    ``created_at > target.created_at`` is false for a row written in the same
    millisecond, so without the ``id != target.id`` half of the clause the
    cascade would delete the target and *keep* the reply that shares its
    timestamp. This fixture makes the two rows byte-identical in time on purpose;
    the only thing that can order them is the tiebreaker.
    """
    fake = FakeSession()
    conversation_id = uuid.uuid4()
    shared = datetime(2026, 10, 21, 12, 0, 0)
    user_msg = _turn(conversation_id, role="user", content="q", offset_ms=0, base=shared)
    reply = _turn(conversation_id, role="assistant", content="a", offset_ms=0, base=shared)
    assert user_msg.created_at == reply.created_at  # the premise of the test
    fake.seed(Message, [user_msg, reply])
    repo = ConversationRepository(fake)

    deleted = await repo.delete_message(user_msg.id, conversation_id)

    assert deleted == 2
    assert fake.rows[Message] == []


@pytest.mark.asyncio
async def test_unknown_message_id_is_a_zero_not_a_cross_tenant_delete():
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake, uuid.uuid4(), [("user", "q"), ("assistant", "a")]
    )
    repo = ConversationRepository(fake)

    deleted = await repo.delete_message(uuid.uuid4(), conversation_id)

    assert deleted == 0
    assert len(fake.rows[Message]) == 2
    assert {m.id for m in fake.rows[Message]} == {r.id for r in rows}


@pytest.mark.asyncio
async def test_message_from_another_conversation_is_not_deleted():
    fake = FakeSession()
    other_conv = uuid.uuid4()
    stranger = _turn(other_conv, role="user", content="theirs", offset_ms=0)
    fake.seed(Message, [stranger])
    repo = ConversationRepository(fake)

    deleted = await repo.delete_message(stranger.id, uuid.uuid4())

    assert deleted == 0
    assert fake.rows[Message] == [stranger]


# ─────────────────────────────────────────────────────────────────────────────
# Repository: FK-safe deletion (the live 500)
#
# The FakeSession enforces no constraints, so these tests pin the *statements*
# the repository issues -- table by table, and the order between them. That
# ordering is the entire fix: children before messages, and one bulk DELETE for
# the messages themselves (a single statement is self-consistent for the
# self-referencing parent_message_id; N row-wise ORM deletes are not).
# ─────────────────────────────────────────────────────────────────────────────


def _delete_tables(fake: FakeSession) -> list[str]:
    """Table name of each bulk DELETE, in the order they were issued."""
    return [getattr(s.table, "name", None) for s in fake.delete_statements]


def _message_deletes(fake: FakeSession) -> list:
    return [s for s in fake.delete_statements if getattr(s.table, "name", None) == "messages"]


def _in_ids(statement) -> set[uuid.UUID]:
    """Ids bound to the statement's ``IN`` criterion (none when absent)."""
    for criterion in statement._where_criteria:
        right = getattr(criterion, "right", None)
        value = getattr(right, "value", None)
        if isinstance(value, (list, tuple, set)):
            return set(value)
    return set()


def _seed_fk_children(fake: FakeSession, message_id: uuid.UUID, conversation_id: uuid.UUID) -> dict[str, object]:
    """One row in every table with an FK into ``messages`` -- the full surface
    that made the row-wise deletes explode. ``AgentRun.message_id`` is not an
    FK (plain audit column) and is deliberately absent."""
    attachment = MessageAttachment(
        message_id=message_id, file_id=uuid.uuid4(), filename="x.pdf", file_type="application/pdf"
    )
    tool_call = ToolCall(conversation_id=conversation_id, message_id=message_id, tool_name="web_search")
    branch = ConversationBranch(
        conversation_id=uuid.uuid4(), parent_conversation_id=conversation_id, fork_message_id=message_id
    )
    artifact = Artifact(user_id=uuid.uuid4(), message_id=message_id, title="doc", content="# doc")
    version = ArtifactVersion(artifact_id=artifact.id, version=1, title="doc", content="# doc")
    fake.seed(MessageAttachment, [attachment])
    fake.seed(ToolCall, [tool_call])
    fake.seed(ConversationBranch, [branch])
    fake.seed(Artifact, [artifact])
    fake.seed(ArtifactVersion, [version])
    return {
        "attachment": attachment,
        "tool_call": tool_call,
        "branch": branch,
        "artifact": artifact,
        "version": version,
    }


@pytest.mark.asyncio
async def test_delete_purges_every_child_table_and_orders_them_before_the_messages():
    """All five FKs into ``messages`` are removed, in dependency order.

    Ordering is the assertion that matters, and it is asserted by *position*:
    a fix that purged children after the messages (or not at all) still emptied
    every child table in the end on a database that checks nothing, and would
    500 again on the real one.
    """
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake, uuid.uuid4(), [("user", "q"), ("assistant", "a")]
    )
    children = _seed_fk_children(fake, rows[1].id, conversation_id)
    repo = ConversationRepository(fake)

    deleted = await repo.delete_message(rows[0].id, conversation_id)

    assert deleted == 2
    assert fake.rows[MessageAttachment] == []
    assert fake.rows[ToolCall] == []
    assert fake.rows[ConversationBranch] == []
    assert fake.rows[Artifact] == []
    assert fake.rows[ArtifactVersion] == []
    assert fake.rows[Message] == []
    assert children  # fixture premise: every FK surface was actually seeded

    tables = _delete_tables(fake)
    msg_idx = tables.index("messages")
    for child_table in ("message_attachments", "tool_calls", "conversation_branches", "artifacts", "artifact_versions"):
        assert child_table in tables, f"no bulk delete issued for {child_table}"
        assert tables.index(child_table) < msg_idx, f"{child_table} deleted after messages"


@pytest.mark.asyncio
async def test_delete_issues_exactly_one_bulk_statement_covering_target_and_cascade():
    """One ``DELETE ... WHERE id IN (target, victims...)`` for the messages.

    This is the self-referencing-FK fix proper. Postgres checks a NO ACTION
    constraint when the statement completes, so a parent and its
    ``parent_message_id`` child can leave in the same statement -- while the
    row-wise ORM deletes this replaced flushed the parent first and raised
    ``ForeignKeyViolation`` (observed live: Delete button -> 500).
    """
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake,
        uuid.uuid4(),
        [
            ("user", "q1"),
            ("assistant", "a1"),
            ("user", "q2"),
            ("assistant", "a2"),
        ],
    )
    # Mirror the real transcript shape: replies carry a parent link to their
    # prompt -- exactly the rows whose deletion violated the FK live.
    rows[1].parent_message_id = rows[0].id
    rows[3].parent_message_id = rows[2].id
    repo = ConversationRepository(fake)

    deleted = await repo.delete_message(rows[0].id, conversation_id)

    assert deleted == 4
    message_deletes = _message_deletes(fake)
    assert len(message_deletes) == 1, "messages must be deleted by one statement, not row-wise"
    assert _in_ids(message_deletes[0]) == {r.id for r in rows}


@pytest.mark.asyncio
async def test_single_statement_delete_of_a_parent_and_its_linked_child_is_the_shape_postgres_accepts():
    """Deleting only the parent link-holder still goes through one statement.

    Regenerate deletes a lone assistant reply whose ``parent_message_id``
    points at a *surviving* prompt: the child goes, the parent stays. The
    statement shape must not change with the set size -- the invariant the
    previous test pins for cascades holds here too.
    """
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake, uuid.uuid4(), [("user", "q"), ("assistant", "a")]
    )
    rows[1].parent_message_id = rows[0].id
    repo = ConversationRepository(fake)

    deleted = await repo.delete_message(rows[1].id, conversation_id)

    assert deleted == 1
    message_deletes = _message_deletes(fake)
    assert len(message_deletes) == 1
    assert _in_ids(message_deletes[0]) == {rows[1].id}
    survivors = {m.id for m in fake.rows[Message]}
    assert survivors == {rows[0].id}


@pytest.mark.asyncio
async def test_edit_truncate_purges_child_rows_of_the_turns_it_discards():
    """The time-travel cascade had the identical defect.

    Editing a prompt discards the answer it produced -- an answer that routinely
    owns tool_calls/attachment rows. The edit path is exercised live far more
    than delete, and it only avoided the 500 so far because the test
    conversation's rows carried no children.
    """
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake, uuid.uuid4(), [("user", "typo q"), ("assistant", "old answer"), ("user", "later")]
    )
    children = _seed_fk_children(fake, rows[1].id, conversation_id)
    repo = ConversationRepository(fake)

    edited = await repo.update_message_content(rows[0].id, "fixed q", conversation_id)

    assert edited is not None
    assert fake.rows[ToolCall] == []
    assert fake.rows[MessageAttachment] == []
    assert fake.rows[Message] == [rows[0]]
    tables = _delete_tables(fake)
    assert tables.index("tool_calls") < tables.index("messages")
    assert tables.index("message_attachments") < tables.index("messages")
    assert children


# ─────────────────────────────────────────────────────────────────────────────
# Repository: edit
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_rewrites_text_and_truncates_the_turns_it_invalidated():
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake,
        uuid.uuid4(),
        [("user", "typo q"), ("assistant", "old answer"), ("user", "later"), ("assistant", "later a")],
    )
    repo = ConversationRepository(fake)

    edited = await repo.update_message_content(rows[0].id, "fixed q", conversation_id)

    assert edited is not None
    assert edited.content == "fixed q"
    survivors = [m.id for m in fake.rows[Message]]
    assert survivors == [rows[0].id]


@pytest.mark.asyncio
async def test_edit_keeps_the_rows_place_in_the_transcript():
    """The edit must not move the turn to the end.

    ``messages`` has no ``updated_at`` column, so the row's ordering field is
    ``created_at`` and the cascade compares on it. Bumping it to "now" would both
    move the edited turn after its own descendants *and* make the truncation
    miss them.
    """
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake, uuid.uuid4(), [("user", "q"), ("assistant", "a")]
    )
    original_created = rows[0].created_at
    repo = ConversationRepository(fake)

    edited = await repo.update_message_content(rows[0].id, "q2", conversation_id)

    assert edited is not None
    assert edited.created_at == original_created


@pytest.mark.asyncio
async def test_edit_of_unknown_message_returns_none():
    fake = FakeSession()
    conversation_id, _ = _seed_turn(fake, uuid.uuid4(), [("user", "q")])
    repo = ConversationRepository(fake)

    assert await repo.update_message_content(uuid.uuid4(), "x", conversation_id) is None


@pytest.mark.asyncio
async def test_edit_can_be_scoped_to_the_last_turn_without_truncating():
    """``truncate_after=False`` edits in place — the seam a future branch uses."""
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake, uuid.uuid4(), [("user", "q"), ("assistant", "a"), ("user", "next")]
    )
    repo = ConversationRepository(fake)

    edited = await repo.update_message_content(
        rows[0].id, "q2", conversation_id, truncate_after=False
    )

    assert edited is not None
    assert len(fake.rows[Message]) == 3  # nothing removed


# ─────────────────────────────────────────────────────────────────────────────
# Service: rules
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_service_delete_reports_the_row_count_and_404s_on_a_miss():
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake, uuid.uuid4(), [("user", "q"), ("assistant", "a")]
    )
    svc = ConversationService(fake)

    assert await svc.delete_message(rows[0].id, conversation_id) == 2
    with pytest.raises(ResourceNotFoundError):
        await svc.delete_message(rows[0].id, conversation_id)


@pytest.mark.asyncio
async def test_service_edit_404s_on_a_miss_and_returns_the_row_on_a_hit():
    fake = FakeSession()
    conversation_id, rows = _seed_turn(fake, uuid.uuid4(), [("user", "q")])
    svc = ConversationService(fake)

    edited = await svc.edit_message(rows[0].id, "q2", conversation_id)
    assert edited.content == "q2"
    with pytest.raises(ResourceNotFoundError):
        await svc.edit_message(uuid.uuid4(), "x", conversation_id)


@pytest.mark.asyncio
async def test_regenerate_returns_the_stored_prompt_and_drops_the_reply():
    """The prompt comes from the *database*, not the caller.

    That is what makes a regenerate work after a reload: the client may have no
    copy of the text at all, and the server reads the turn that produced the
    reply before deleting it.
    """
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake, uuid.uuid4(), [("user", "what is the capital"), ("assistant", "Paris")]
    )
    svc = ConversationService(fake)

    target = await svc.prepare_regeneration(rows[1].id, conversation_id)

    assert target.prompt == "what is the capital"
    # The id travels with the text: the client resends this same row, and the
    # stream route must adopt it rather than append a second copy.
    assert target.user_message_id == rows[0].id
    survivors = {m.id for m in fake.rows[Message]}
    assert survivors == {rows[0].id}  # prompt kept, stale reply gone


@pytest.mark.asyncio
async def test_regenerate_refuses_a_user_turn():
    """Regenerating a user turn is just "ask again", so it is not allowed.

    The assertion is on the *message*, not merely on the exception type: both the
    role guard and the "no preceding prompt" guard raise ``ValueError``, and for a
    first-and-only user turn the second one fires too. A bare ``pytest.raises``
    therefore passed with the role guard deleted — the test was green for the
    wrong reason, and the revert harness is what caught it (§9.13).
    """
    fake = FakeSession()
    conversation_id, rows = _seed_turn(fake, uuid.uuid4(), [("user", "q")])
    svc = ConversationService(fake)

    with pytest.raises(ValueError, match="[Oo]nly an assistant message"):
        await svc.prepare_regeneration(rows[0].id, conversation_id)
    # The refusal must not have deleted anything.
    assert fake.rows[Message] == [rows[0]]


@pytest.mark.asyncio
async def test_regenerate_refuses_a_user_turn_even_with_a_prompt_above_it():
    """The role guard is load-bearing independently of the "no prompt" guard.

    With a *preceding* user turn present, deleting the role check would let the
    second user turn through and return the first turn's text — a silently wrong
    regenerate rather than an error. This fixture isolates the role check from the
    missing-prompt path so the two cannot stand in for each other.
    """
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake, uuid.uuid4(), [("user", "first"), ("user", "second")]
    )
    svc = ConversationService(fake)

    with pytest.raises(ValueError, match="[Oo]nly an assistant message"):
        await svc.prepare_regeneration(rows[1].id, conversation_id)
    assert len(fake.rows[Message]) == 2  # nothing deleted


@pytest.mark.asyncio
async def test_regenerate_404s_on_an_unknown_message():
    fake = FakeSession()
    conversation_id, _ = _seed_turn(
        fake, uuid.uuid4(), [("user", "q"), ("assistant", "a")]
    )
    svc = ConversationService(fake)

    with pytest.raises(ResourceNotFoundError):
        await svc.prepare_regeneration(uuid.uuid4(), conversation_id)


@pytest.mark.asyncio
async def test_regenerate_returns_the_nearest_preceding_prompt():
    """The prompt is the **last** user turn before the reply, not the first.

    Two properties live in the same four lines, and both need a fixture that
    makes them independently observable:

    * the read happens **before** the delete -- otherwise the loop runs past the
      (now absent) reply to the end of history (§9.13);
    * among the turns before it, the loop keeps the **nearest** prompt, not the
      first. A conversation with two prior user turns that kept the first would
      resend the wrong question under a regenerated answer.

    This fixture has two preceding user turns, so "first wins" returns
    ``"oldest question"`` and only "last wins" returns ``"the prompt that
    produced it"``.
    """
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake,
        uuid.uuid4(),
        [
            ("user", "oldest question"),
            ("assistant", "oldest answer"),
            ("user", "the prompt that produced it"),
            ("assistant", "the reply being regenerated"),
        ],
    )
    svc = ConversationService(fake)

    target = await svc.prepare_regeneration(rows[3].id, conversation_id)

    assert target.prompt == "the prompt that produced it"
    assert target.user_message_id == rows[2].id
    # Only the reply is removed (it is the last turn, so nothing follows it).
    assert [m.id for m in fake.rows[Message]] == [rows[0].id, rows[1].id, rows[2].id]


# ─────────────────────────────────────────────────────────────────────────────
# Service: resend adoption (the duplicate-turn fix)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reuse_adopts_the_existing_row_instead_of_appending_a_duplicate():
    """The whole defect in one assertion: a resend must not grow the transcript.

    The stream route persists the user turn itself, so regenerate/edit followed
    by a resend used to store the question twice. The client sends the id back
    and the service adopts that row; ``FakeSession.added`` is the witness that no
    second row was created.
    """
    fake = FakeSession()
    conversation_id, rows = _seed_turn(fake, uuid.uuid4(), [("user", "q")])
    svc = ConversationService(fake)

    reused = await svc.reuse_user_turn(conversation_id, "q", rows[0].id)

    assert reused.id == rows[0].id
    assert len(fake.rows[Message]) == 1
    assert fake.added == []  # nothing appended
    assert "q" in [r.content for r in fake.rows[Message]]


@pytest.mark.asyncio
async def test_reuse_updates_the_text_when_it_differs_without_truncating():
    fake = FakeSession()
    conversation_id, rows = _seed_turn(fake, uuid.uuid4(), [("user", "old")])
    svc = ConversationService(fake)

    reused = await svc.reuse_user_turn(conversation_id, "new", rows[0].id)

    assert reused.content == "new"
    assert [m.content for m in fake.rows[Message]] == ["new"]


@pytest.mark.asyncio
async def test_reuse_refuses_a_row_that_is_not_the_latest_turn():
    """A stale id is a client/server disagreement, not a licence to rewrite history.

    The reply exists, so the prompt is not the newest row. Falling back to an
    append here would recreate the duplicate; deleting forward would destroy a
    turn the client never asked to remove. The only safe answer is a refusal.
    """
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake, uuid.uuid4(), [("user", "q"), ("assistant", "a")]
    )
    svc = ConversationService(fake)

    with pytest.raises(ValueError, match="latest turn"):
        await svc.reuse_user_turn(conversation_id, "q", rows[0].id)

    # Nothing was appended, deleted or rewritten.
    assert [m.id for m in fake.rows[Message]] == [rows[0].id, rows[1].id]
    assert fake.added == []


@pytest.mark.asyncio
async def test_reuse_refuses_an_assistant_row():
    """Only a user turn can be re-asked; reusing a reply would store it as input."""
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake, uuid.uuid4(), [("user", "q"), ("assistant", "a")]
    )
    svc = ConversationService(fake)

    with pytest.raises(ValueError, match="[Oo]nly a user message"):
        await svc.reuse_user_turn(conversation_id, "a", rows[1].id)

    assert [m.id for m in fake.rows[Message]] == [rows[0].id, rows[1].id]


@pytest.mark.asyncio
async def test_reuse_404s_on_an_unknown_or_foreign_message():
    fake = FakeSession()
    conversation_id, _ = _seed_turn(fake, uuid.uuid4(), [("user", "q")])
    stranger = _turn(uuid.uuid4(), role="user", content="theirs", offset_ms=0)
    fake.seed(Message, [stranger])
    svc = ConversationService(fake)

    with pytest.raises(ResourceNotFoundError):
        await svc.reuse_user_turn(conversation_id, "q", uuid.uuid4())
    with pytest.raises(ResourceNotFoundError):
        await svc.reuse_user_turn(conversation_id, "theirs", stranger.id)


@pytest.mark.asyncio
async def test_regenerate_then_reuse_leaves_one_prompt_row_not_two():
    """The measured live defect: regenerate + resend produced 5 rows, not 4.

    This drives the exact server-side sequence the hook performs -- prepare the
    regeneration, then re-send the returned prompt with the returned id -- and
    asserts the transcript did not grow a duplicate question.
    """
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake, uuid.uuid4(), [("user", "q"), ("assistant", "a")]
    )
    svc = ConversationService(fake)

    target = await svc.prepare_regeneration(rows[1].id, conversation_id)
    reused = await svc.reuse_user_turn(
        conversation_id, target.prompt, target.user_message_id
    )

    assert reused.id == rows[0].id
    assert [m.id for m in fake.rows[Message]] == [rows[0].id]
    assert fake.added == []


@pytest.mark.asyncio
async def test_edit_then_reuse_keeps_the_edited_row_and_its_place():
    fake = FakeSession()
    conversation_id, rows = _seed_turn(
        fake,
        uuid.uuid4(),
        [("user", "typo"), ("assistant", "old answer"), ("user", "later")],
    )
    svc = ConversationService(fake)

    edited = await svc.edit_message(rows[0].id, "fixed", conversation_id)
    reused = await svc.reuse_user_turn(conversation_id, "fixed", edited.id)

    assert reused.id == rows[0].id
    assert reused.created_at == rows[0].created_at
    assert [m.id for m in fake.rows[Message]] == [rows[0].id]
    assert [m.id for m in fake.rows[Message]].count(rows[0].id) == 1

