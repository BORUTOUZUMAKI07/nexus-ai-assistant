"""
Unit tests for read-only conversation sharing: owner-gated minting, public
reads, expiry/deactivation, and revocation.
"""
import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from backend.app.domain.conversation.models import Conversation, Message
from backend.app.domain.share.models import ConversationShare
from backend.app.services.share_service import ShareService
from fakes import FakeSession


def _await(coro):
    # pytest-asyncio tears the current loop down after each async test, so once
    # one has run get_event_loop() raises here. Re-establish a loop instead of
    # depending on collection order.
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _make_session(owner, share=None, expired=False, inactive=False):
    fake = FakeSession()
    conv = Conversation(user_id=owner, title="Shared notes")
    fake.seed(Conversation, [conv])
    fake.seed(
        Message,
        [
            Message(conversation_id=conv.id, role="user", content="question"),
            Message(conversation_id=conv.id, role="assistant", content="answer"),
        ],
    )
    if share:
        expires = None
        if expired:
            expires = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
        fake.seed(
            ConversationShare,
            [
                ConversationShare(
                    conversation_id=conv.id,
                    created_by=owner,
                    token=share,
                    is_active=not inactive,
                    expires_at=expires,
                )
            ],
        )
    return fake, conv


def test_create_share_requires_ownership():
    owner, other = uuid4(), uuid4()
    fake, conv = _make_session(owner)
    with pytest.raises(ValueError):
        _await(ShareService(fake).create(other, conv.id))

    share = _await(ShareService(fake).create(owner, conv.id, ttl_seconds=3600))
    assert share.token
    assert share.expires_at is not None
    assert share.created_by == owner


def test_read_public_returns_conversation_content():
    owner = uuid4()
    fake, _ = _make_session(owner)
    share = _await(ShareService(fake).create(owner, fake.rows[Conversation][0].id))
    payload = _await(ShareService(fake).read_public(share.token))
    assert payload["conversation"]["title"] == "Shared notes"
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant"]
    assert payload["messages"][1]["content"] == "answer"


def test_read_public_strips_the_source_excerpt_and_filename_from_citations():
    """#7 — a public share must not publish private file excerpts or names.

    The stored citation carries `content_snippet` (a verbatim excerpt of the
    owner's private file) and `filename`. Both are dropped before a public
    reader ever sees them; the position, chunk index and score survive so the
    answer's source numbering still lines up. Reverting the projection at the
    `read_public` call site puts the excerpt back in the payload and this test
    fails.
    """
    owner = uuid4()
    fake = FakeSession()
    conv = Conversation(user_id=owner, title="Shared notes")
    fake.seed(Conversation, [conv])
    fake.seed(
        Message,
        [
            Message(
                conversation_id=conv.id,
                role="assistant",
                content="answer",
                citations=[
                    {
                        "type": "citation",
                        "filename": "private-salary.pdf",
                        "chunk_index": 2,
                        "score": 0.91,
                        "content_snippet": "Salary band for Alice is 240000 USD",
                    },
                    {
                        "type": "citation",
                        "filename": "private-roadmap.pdf",
                        "chunk_index": 7,
                        "score": 0.5,
                        "content_snippet": "Unreleased Q4 roadmap and headcount",
                    },
                ],
            )
        ],
    )
    fake.seed(
        ConversationShare,
        [ConversationShare(conversation_id=conv.id, created_by=owner, token="tok-cites")],
    )

    payload = _await(ShareService(fake).read_public("tok-cites"))
    citations = payload["messages"][0]["citations"]

    # Positions, chunk indices and scores are preserved -- the source numbering
    # the answer refers to still resolves.
    assert len(citations) == 2
    assert [c["chunk_index"] for c in citations] == [2, 7]
    assert [c["score"] for c in citations] == [0.91, 0.5]

    # The private detail is gone from the whole payload, not merely from the key
    # the client happens to read.
    for citation in citations:
        assert "content_snippet" not in citation
        assert "filename" not in citation
    blob = json.dumps(payload)
    assert "private-salary.pdf" not in blob
    assert "private-roadmap.pdf" not in blob
    assert "Salary band for Alice" not in blob
    assert "Unreleased Q4 roadmap" not in blob


def test_read_public_unknown_or_inactive_or_expired():
    owner = uuid4()

    fake, _ = _make_session(owner)
    assert _await(ShareService(fake).read_public("garbage-token")) is None

    fake2, _ = _make_session(owner, share="tok-exp", expired=True)
    assert _await(ShareService(fake2).read_public("tok-exp")) is None

    fake3, _ = _make_session(owner, share="tok-off", inactive=True)
    assert _await(ShareService(fake3).read_public("tok-off")) is None


def test_revoke_disables_links():
    owner = uuid4()
    fake, conv = _make_session(owner, share="tok-live")
    svc = ShareService(fake)
    assert len(_await(svc.list_for_conversation(owner, conv.id))) == 1

    assert _await(svc.revoke(owner, conv.id)) is True
    # delete was executed -> the store no longer holds the share
    assert fake.rows.get(ConversationShare, []) == []
    assert _await(svc.read_public("tok-live")) is None


def test_revoke_by_non_owner_is_rejected():
    owner, other = uuid4(), uuid4()
    fake, conv = _make_session(owner, share="tok-owner")
    assert _await(ShareService(fake).revoke(other, conv.id)) is False
    assert len(_await(ShareService(fake).list_for_conversation(owner, conv.id))) == 1
