"""
Unit tests for the GDPR account service: portable export (no raw secrets in the
payload) and dependency-ordered right-to-erasure cascade.
"""
import asyncio
import json
from uuid import uuid4

import pytest
from backend.app.domain.conversation.models import Conversation, Message
from backend.app.domain.file.models import File
from backend.app.domain.usage.models import UsageLog
from backend.app.domain.user.models import APIKey, User, UserMemory, UserSettings
from backend.app.services import account_service as account_module
from backend.app.services.account_service import AccountService
from fakes import FakeSession


def _await(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _seed_user_data(fake: FakeSession) -> tuple[User, Conversation]:
    user = User(email="gdpr@example.com", username="gdpr.user")
    fake.seed(User, [user])

    settings = UserSettings(user_id=user.id)
    fake.seed(UserSettings, [settings])

    conv = Conversation(user_id=user.id, title="GDPR conversation")
    fake.seed(Conversation, [conv])
    msg1 = Message(conversation_id=conv.id, role="user", content="hello")
    msg2 = Message(
        conversation_id=conv.id,
        role="assistant",
        content="hi there",
        citations=[{"document": "a.pdf"}],
        user_feedback="good",
    )
    fake.seed(Message, [msg1, msg2])

    fake.seed(
        File,
        [
            File(
                user_id=user.id,
                conversation_id=conv.id,
                filename="doc.pdf",
                original_filename="doc.pdf",
                file_type="pdf",
                mime_type="application/pdf",
                size_bytes=2048,
                storage_path="private/user/doc.pdf",
                status="indexed",
            )
        ],
    )

    fake.seed(
        UserMemory,
        [UserMemory(user_id=user.id, category="preference", content="likes dark theme")],
    )
    fake.seed(
        APIKey,
        [
            APIKey(
                user_id=user.id,
                provider="openai",
                encrypted_key="encrypted-secret",
                key_preview="abcd",
                label="dev key",
            )
        ],
    )
    fake.seed(
        UsageLog,
        [
            UsageLog(user_id=user.id, model="complex_reasoning", total_tokens=100, cost_usd=0.01),
            UsageLog(user_id=user.id, model="complex_reasoning", total_tokens=200, cost_usd=0.02),
        ],
    )
    return user, conv


def test_export_is_portable_and_leaks_no_secrets():
    fake = FakeSession()
    user, conv = _seed_user_data(fake)
    payload = _await(AccountService(fake).export_user_data(user.id))

    assert payload["schema_version"] == "1.0"
    assert payload["user"]["email"] == user.email
    assert len(payload["conversations"]) == 1
    conv_payload = payload["conversations"][0]
    assert conv_payload["title"] == "GDPR conversation"
    assert [m["role"] for m in conv_payload["messages"]] == ["user", "assistant"]
    assert conv_payload["messages"][1]["citations"] == [{"document": "a.pdf"}]
    assert conv_payload["messages"][1]["feedback"] == "good"

    assert payload["files"][0]["filename"] == "doc.pdf"
    # storage location is an implementation detail, not exportable user data
    assert "storage_path" not in payload["files"][0]

    # No raw encrypted key material anywhere in the export.
    raw = json.dumps(payload)
    assert "encrypted-secret" not in raw
    assert "encrypted_key" not in raw
    assert payload["api_keys"][0]["key_preview"] == "abcd"

    assert payload["usage_summary"]["calls"] == 2
    assert payload["usage_summary"]["total_tokens"] == 300
    assert payload["usage_summary"]["total_cost_usd"] == pytest.approx(0.03)


def test_export_as_json_bytes_roundtrips():
    fake = FakeSession()
    user, _ = _seed_user_data(fake)
    raw_bytes = _await(AccountService(fake).export_as_json_bytes(user.id))
    payload = json.loads(raw_bytes)
    assert payload["user"]["email"] == user.email


class FakeConvRepo:
    def __init__(self):
        self.deleted_ids = []

    async def delete(self, conv):
        self.deleted_ids.append(conv.id)


class FakeVectorStore:
    def __init__(self):
        self.purged = []

    async def delete_by_filter(self, filter_conditions):
        self.purged.append(filter_conditions)


class FakeStorage:
    def __init__(self, fail: bool = False):
        self.deleted_paths = []
        self.fail = fail

    async def delete(self, storage_path):
        if self.fail:
            raise RuntimeError("storage down")
        self.deleted_paths.append(storage_path)
        return True


def test_delete_account_cascades(monkeypatch):
    fake = FakeSession()
    user, conv = _seed_user_data(fake)
    conv2 = Conversation(user_id=user.id, title="Second")
    fake.seed(Conversation, [conv2])

    fake_conv_repo = FakeConvRepo()
    monkeypatch.setattr(account_module, "ConversationRepository", lambda session: fake_conv_repo)

    vector_store = FakeVectorStore()
    storage = FakeStorage()
    _await(AccountService(fake, vector_store=vector_store, storage=storage).delete_account(user.id))

    assert sorted(map(str, fake_conv_repo.deleted_ids)) == sorted(map(str, [conv.id, conv2.id]))
    assert user in fake.deleted  # the users row itself is erased
    # Deletes were issued for the dependent tables (usage, files, webhooks, orgs…).
    assert fake.delete_statements
    assert fake.commits >= 1  # transaction applied
    # User-scoped rows removed from the store (usage logs, memories, api keys, settings…).
    assert fake.rows.get(UsageLog) == []
    assert fake.rows.get(UserMemory) == []
    assert fake.rows.get(APIKey) == []
    assert fake.rows.get(UserSettings) == []
    # Cross-store saga: Qdrant purged by user filter; object blob deleted.
    assert vector_store.purged == [{"user_id": str(user.id)}]
    assert "private/user/doc.pdf" in storage.deleted_paths


def test_delete_account_fail_open_on_infra_outage(monkeypatch):
    fake = FakeSession()
    user, _ = _seed_user_data(fake)
    fake_conv_repo = FakeConvRepo()
    monkeypatch.setattr(account_module, "ConversationRepository", lambda session: fake_conv_repo)

    class _ExplodingVector:
        async def delete_by_filter(self, filter_conditions):
            raise RuntimeError("qdrant down")

    storage = FakeStorage(fail=True)
    _await(
        AccountService(fake, vector_store=_ExplodingVector(), storage=storage).delete_account(user.id)
    )

    # GDPR DB erasure succeeds even though Qdrant + storage are unavailable.
    assert user in fake.deleted
    assert fake.commits >= 1


def test_delete_unknown_user_raises():
    fake = FakeSession()
    with pytest.raises(ValueError):
        _await(AccountService(fake).delete_account(uuid4()))
