"""Repository-tier integration tests on the in-memory sqlite database.

These cover JSONB and ARRAY column round-trips plus the aggregate/rollup
code paths that the pure unit mock-suite cannot exercise against a real DB.
"""
import uuid

import pytest
from backend.app.domain.conversation.repository import ConversationRepository
from backend.app.domain.file.repository import FileRepository
from backend.app.domain.prompt.repository import PromptRepository
from backend.app.domain.system.repository import SystemRepository
from backend.app.domain.system.schemas import AuditLogCreate
from backend.app.domain.tool.repository import ToolRepository
from backend.app.domain.usage.models import CostLog
from backend.app.domain.usage.repository import UsageRepository
from backend.app.domain.usage.schemas import EvaluationLogCreate, UsageLogCreate
from backend.app.domain.user.repository import UserRepository
from backend.app.domain.user.schemas import UserCreate


@pytest.mark.asyncio
async def test_user_create_settings_and_memory_embedding_roundtrip(
    db_session,
):
    repo = UserRepository(db_session)
    user = await repo.create(
        UserCreate(
            email=f"repo-{uuid.uuid4().hex[:8]}@example.com",
            username=f"repo-{uuid.uuid4().hex[:8]}",
            password="StrongPass123!",
        )
    )

    settings = await repo.get_settings(user.id)
    assert settings is not None
    assert settings.theme == "dark"

    memory = await repo.create_memory(user.id, "Likes pandas", category="preference")
    memory.embedding = [0.128, -0.455, 0.999]
    db_session.add(memory)
    await db_session.commit()
    await db_session.refresh(memory)
    assert list(memory.embedding) == [0.128, -0.455, 0.999]

    fetched = await repo.get_by_email(user.email)
    assert fetched.id == user.id


@pytest.mark.asyncio
async def test_conversation_add_message_jsonb_fields_roundtrip(db_session, test_user):
    repo = ConversationRepository(db_session)
    conv = await repo.create(user_id=test_user.id, title="Repo Chat")

    msg = await repo.add_message(
        conversation_id=conv.id,
        role="assistant",
        content="answer",
        citations=[{"file_id": str(uuid.uuid4()), "score": 0.9}],
        tool_calls=[{"name": "web_search", "arguments": {"q": "x"}}],
        metadata_json={"quality": {"score": 0.8}},
    )
    assert len(msg.citations) == 1
    assert msg.tool_calls[0]["name"] == "web_search"
    assert msg.metadata_json["quality"]["score"] == 0.8

    history = await repo.get_messages(conv.id)
    assert len(history) == 1

    conv2 = await repo.get_by_id(conv.id)
    assert conv2.token_count == 0

    forked = await repo.fork_conversation(
        user_id=test_user.id,
        parent_conv_id=conv.id,
        fork_message_id=msg.id,
        branch_name="repo-branch",
    )
    forked_messages = await repo.get_messages(forked.id)
    assert len(forked_messages) == 1
    assert forked_messages[0].citations == msg.citations


@pytest.mark.asyncio
async def test_usage_repo_summary_and_cost_rollup(db_session, test_user):
    repo = UsageRepository(db_session)

    await repo.log_usage(
        UsageLogCreate(
            user_id=test_user.id,
            model="llama-3.3-70b-versatile",
            prompt_tokens=200,
            completion_tokens=80,
            latency_ms=150.5,
        )
    )
    summary = await repo.get_summary(test_user.id)
    assert summary.total_requests == 1
    assert summary.total_tokens == 280

    cost = CostLog(
        user_id=test_user.id,
        provider="groq",
        model="llama-3.3-70b-versatile",
        input_cost=0.0001,
        output_cost=0.0002,
        total_cost=0.0003,
        billing_period="2026-09",
    )
    created_cost = await repo.create(cost)
    assert created_cost.total_cost == 0.0003

    await repo.log_evaluation(
        EvaluationLogCreate(
            trace_id="trace-9",
            metric_name="offline_rag_recall",
            score=0.75,
            passed=True,
        )
    )
    evals = await repo.get_evaluations(limit=10)
    assert any(e.metric_name == "offline_rag_recall" for e in evals)


@pytest.mark.asyncio
async def test_prompt_template_versioning(db_session, test_user):
    repo = PromptRepository(db_session)
    template = await repo.create_template(
        user_id=test_user.id,
        title="System Architect",
        category="architecture",
        system_prompt="You are a staff architect.",
        input_variables=["constraints"],
        is_public=False,
    )
    assert template.version == 1
    assert template.input_variables == ["constraints"]

    updated = await repo.update_template(
        template,
        {"system_prompt": "You are a principal architect."},
        user_id=test_user.id,
    )
    assert updated.version == 2

    templates = await repo.get_templates(test_user.id, include_public=False)
    assert len(templates) == 1


@pytest.mark.asyncio
async def test_tool_repo_schema_and_call_roundtrip(db_session, test_user):
    repo = ToolRepository(db_session)
    conv = await ConversationRepository(db_session).create(
        user_id=test_user.id, title="Tool Repo Chat"
    )
    tool = await repo.register_tool(
        name="web_search",
        description="Search the web",
        category="research",
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
        requires_approval=True,
    )
    assert tool.parameters_schema["properties"]["query"]["type"] == "string"
    assert tool.requires_approval is True

    call = await repo.log_tool_call(
        conversation_id=conv.id,
        tool_name="web_search",
        input_args={"query": "nested {'dict': [1, 2]}"},
        status="pending",
        requires_approval=True,
    )
    call = await repo.update_tool_call(
        call.id,
        status="completed",
        output_result={"hits": [{"title": "a"}]},
        is_approved=True,
    )
    refreshed = await repo.get_tool_call(call.id)
    assert refreshed.status == "completed"
    assert refreshed.output_result == {"hits": [{"title": "a"}]}
    assert refreshed.input_args == {"query": "nested {'dict': [1, 2]}"}


@pytest.mark.asyncio
async def test_system_config_audit_roundtrip(db_session, test_user):
    repo = SystemRepository(db_session)
    cfg = await repo.set_config(
        key="maintenance_mode",
        value={"enabled": False, "reason": "none"},
        category="general",
        updated_by=test_user.id,
    )
    assert cfg.value_json == {"enabled": False, "reason": "none"}

    await repo.set_config(
        key="maintenance_mode",
        value={"enabled": True, "reason": "deploy"},
        updated_by=test_user.id,
    )
    updated = await repo.get_config("maintenance_mode")
    assert updated.value_json == {"enabled": True, "reason": "deploy"}

    audit = await repo.log_audit(
        AuditLogCreate(
            user_id=test_user.id,
            action="system.config.update",
            resource_type="system",
            status="success",
            details={"key": "maintenance_mode", "nested": {"v": 3}},
        )
    )
    assert audit.details["nested"]["v"] == 3

    logs = await repo.get_audit_logs(user_id=test_user.id)
    assert [log.action for log in logs] == ["system.config.update"]


@pytest.mark.asyncio
async def test_file_repo_lifecycle(db_session, test_user):
    repo = FileRepository(db_session)
    db_file = await repo.create_file(
        user_id=test_user.id,
        filename="docs.txt",
        original_filename="docs.txt",
        file_type=".txt",
        mime_type="text/plain",
        size_bytes=100,
        storage_path="user/docs.txt",
    )
    assert db_file.status == "pending"

    added = await repo.add_chunks(
        db_file.id,
        [
            {
                "chunk_index": 0,
                "content": "parent chunk text",
                "token_count": 4,
                "metadata": {"stab": "one"},
            },
            {
                "chunk_index": 1,
                "content": "child chunk text",
                "token_count": 3,
                "metadata": {"stab": "two"},
                "parent_chunk_id": None,
            },
        ],
    )
    assert len(added) == 2

    chunks = await repo.get_chunks_by_file(db_file.id)
    assert [c.chunk_index for c in chunks] == [0, 1]
    assert chunks[0].metadata_json == {"stab": "one"}

    indexed = await repo.update_status(db_file.id, "indexed", chunk_count=2)
    assert indexed.status == "indexed"
    assert indexed.chunk_count == 2

    assert await repo.delete_file(db_file.id, test_user.id) is True
    assert await repo.get_by_id(db_file.id, user_id=test_user.id) is None
