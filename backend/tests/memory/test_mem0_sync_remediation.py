"""
Comprehensive unit tests for mem0 Two-Way Sync, Resilient Fallback,
and Confidence Reinforcement remediation.
"""
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from backend.app.agents.orchestrator.nodes import _reinforce_memories_background
from backend.app.domain.user.models import UserMemory
from backend.app.domain.user.repository import UserRepository
from backend.app.domain.user.schemas import UserMemoryCreate
from backend.app.services.memory.long_term import LongTermMemoryService
from backend.app.services.settings.service import UserSettingsService
from backend.tests.fakes import FakeSession


@pytest.mark.asyncio
async def test_manual_memory_creation_calls_mem0_and_records_id(monkeypatch):
    """Ensure manual memory creation via Settings syncs to mem0 and stores mem0_id."""
    session = FakeSession()
    service = UserSettingsService(session)
    user_id = uuid4()

    fake_mem0_id = "mem0-test-uuid-456"

    async def fake_add(**kwargs):
        return fake_mem0_id

    import backend.app.services.memory.long_term as lt_mod
    monkeypatch.setattr(lt_mod.long_term_memory, "add", fake_add)

    mem_in = UserMemoryCreate(
        content="I prefer TypeScript strict mode",
        category="preference",
    )
    result = await service.add_memory(user_id, mem_in)

    assert result.content == "I prefer TypeScript strict mode"
    assert result.mem0_id == fake_mem0_id
    assert result.is_active is True


@pytest.mark.asyncio
async def test_memory_deletion_purges_from_mem0(monkeypatch):
    """Ensure deleting a memory via Settings deletes from both SQL and mem0."""
    session = FakeSession()
    service = UserSettingsService(session)
    user_id = uuid4()
    memory_id = uuid4()
    mem0_id = "mem0-del-789"

    mem_row = UserMemory(
        id=memory_id,
        user_id=user_id,
        content="Old preference",
        mem0_id=mem0_id,
        is_active=True,
    )
    session.seed(UserMemory, [mem_row])

    deleted_from_mem0 = []

    async def fake_delete(mid):
        deleted_from_mem0.append(mid)
        return True

    import backend.app.services.memory.long_term as lt_mod
    monkeypatch.setattr(lt_mod.long_term_memory, "delete", fake_delete)

    await service.delete_memory(user_id, memory_id)

    assert mem_row.is_active is False
    assert deleted_from_mem0 == [mem0_id]


@pytest.mark.asyncio
async def test_superseded_memory_purges_mem0_vector(monkeypatch):
    """Ensure superseded possessive attributes purge their old vector from mem0."""
    session = FakeSession()
    repo = UserRepository(session)
    user_id = uuid4()
    old_mem0_id = "mem0-timezone-est"

    old_row = UserMemory(
        id=uuid4(),
        user_id=user_id,
        content="My timezone is EST",
        mem0_id=old_mem0_id,
        is_active=True,
    )
    session.seed(UserMemory, [old_row])

    purged = []

    async def fake_delete(mid):
        purged.append(mid)
        return True

    import backend.app.services.memory.long_term as lt_mod
    monkeypatch.setattr(lt_mod.long_term_memory, "delete", fake_delete)

    count = await repo.deactivate_memories_by_contents(user_id, ["My timezone is EST"])

    assert count == 1
    assert old_row.is_active is False
    assert purged == [old_mem0_id]


@pytest.mark.asyncio
async def test_build_memory_context_block_falls_back_to_sql_when_mem0_empty(monkeypatch):
    """Ensure fallback to SQL user_memories when mem0 returns no semantic matches."""
    lt_service = LongTermMemoryService()
    user_id = uuid4()

    # mem0 search returns empty list
    async def fake_search(**kwargs):
        return []

    async def fake_fallback(user_id, limit=8):
        return [
            {
                "id": "db-1",
                "db_id": "db-1",
                "memory": "Always use snake_case for Python variables",
                "category": "preference",
                "confidence": 0.85,
            }
        ]

    monkeypatch.setattr(lt_service, "search", fake_search)
    monkeypatch.setattr(lt_service, "get_fallback_memories", fake_fallback)

    block, recalled = await lt_service.build_memory_context_block_with_ids(
        query="Write a python function",
        user_id=user_id,
        limit=5,
    )

    assert "Always use snake_case for Python variables" in block
    assert len(recalled) == 1
    assert recalled[0]["memory"] == "Always use snake_case for Python variables"


@pytest.mark.asyncio
async def test_reinforce_memories_background_boosts_confidence(monkeypatch):
    """Ensure recalled memories get their confidence reinforced in background."""
    user_id = uuid4()
    mem_uuid = uuid4()


    # mem_row creation removed (unused in test)

    reinforced_ids = []

    class MockRepo:
        def __init__(self, session):
            pass

        async def find_memory_ids_by_mem0_ids(self, mem0_ids):
            return [mem_uuid] if "mem0-fastapi" in mem0_ids else []

        async def reinforce_memories(self, ids):
            reinforced_ids.extend(ids)
            return len(ids)

    class MockSessionCtx:
        async def __aenter__(self):
            return FakeSession()
        async def __aexit__(self, *args):
            pass

    import backend.app.domain.user.repository as repo_mod
    monkeypatch.setattr(repo_mod, "UserRepository", MockRepo)
    import backend.app.infrastructure.database.session as sess_mod
    monkeypatch.setattr(sess_mod, "async_session_factory", lambda: MockSessionCtx())

    recalled_items = [{"id": "mem0-fastapi", "memory": "Prefers FastAPI"}]
    await _reinforce_memories_background(str(user_id), recalled_items)

    assert reinforced_ids == [mem_uuid]


@pytest.mark.asyncio
async def test_delete_all_for_user_calls_client_delete_all():
    """Ensure GDPR delete_all_for_user invokes client delete_all."""
    mock_client = AsyncMock()
    mock_client.delete_all = AsyncMock(return_value={"message": "ok"})
    service = LongTermMemoryService(client=mock_client)

    uid = uuid4()
    result = await service.delete_all_for_user(uid)

    assert result is True
    mock_client.delete_all.assert_awaited_once_with(user_id=str(uid))
