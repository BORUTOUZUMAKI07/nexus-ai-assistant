"""
Unit tests for owner-scoped conversation search (title + message body matching).
"""
import asyncio
from uuid import uuid4

from backend.app.domain.conversation.models import Conversation, Message
from backend.app.services.conversation_service import ConversationService


class FakeSearchRepo:
    def __init__(self, conversations, messages):
        self.conversations = conversations
        self.messages = messages

    async def search_user_conversations(self, user_id, query, limit=20):
        pattern = query.lower()
        matches = []
        for conv in self.conversations:
            if conv.user_id != user_id or conv.is_archived:
                continue
            if pattern in conv.title.lower():
                matches.append(conv)
                continue
            body_hits = [
                m for m in self.messages
                if m.conversation_id == conv.id and pattern in m.content.lower()
            ]
            if body_hits:
                matches.append(conv)
        return matches[:limit]


def _conv(user_id, title):
    return Conversation(user_id=user_id, title=title)


def _msg(conv_id, role, content):
    return Message(conversation_id=conv_id, role=role, content=content)


def _service(user_a, user_b):
    conv_a = _conv(user_a, "Quantum computing notes")
    conv_b = _conv(user_b, "Refund policy draft")  # other user's conversation
    conv_a_body = _conv(user_a, "Trip planning")
    messages = [
        _msg(conv_a.id, "user", "I need a refund for the printer order"),
        _msg(conv_b.id, "user", "travel corpus is leaking quantum details"),  # not user A's
        _msg(conv_a_body.id, "user", "book the quantum hotel in Amsterdam"),
    ]
    repo = FakeSearchRepo([conv_a, conv_b, conv_a_body], messages)
    svc = ConversationService(object())
    svc._repo = repo
    return svc


def _await(coro):
    # Sync test driving a coroutine directly. pytest-asyncio tears the current
    # loop down after each async test, so get_event_loop() raises once one has
    # run. Re-establish a loop instead of depending on collection order.
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def test_search_matches_title_only_for_owner():
    user_a, user_b = uuid4(), uuid4()
    svc = _service(user_a, user_b)

    hits = _await(
        svc.search_conversations(user_a, "quantum", limit=20)
    )
    ids = {str(c.id) for c in hits}
    # title match, but the other user's "quantum"-containing message must not leak
    assert any("Quantum computing" in c.title for c in hits)
    assert all(c.user_id == user_a for c in hits)


def test_search_matches_message_body():
    user_a, user_b = uuid4(), uuid4()
    svc = _service(user_a, user_b)

    hits = _await(
        svc.search_conversations(user_a, "Amsterdam", limit=20)
    )
    assert len(hits) == 1
    assert hits[0].title == "Trip planning"


def test_search_does_not_cross_users():
    user_a, user_b = uuid4(), uuid4()
    svc = _service(user_a, user_b)

    hits = _await(
        svc.search_conversations(user_a, "draft", limit=20)
    )
    # user B's title is the only "draft" match -> user A sees nothing
    assert hits == []