"""
Unit tests for the MCP-style elicitation service (structured human input
reusing the verified single-use HITL claim machinery).
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from backend.app.core.exceptions import ApprovalConsumedError, ApprovalExpiredError, ResourceNotFoundError
from backend.app.services.tools.elicitations import ELICITATION_TOOL_NAME, ElicitationService


class FakeToolCall:
    def __init__(self, tool_call_id, tool_name, created_at, input_args=None, status="pending", is_approved=None):
        self.id = tool_call_id
        self.tool_name = tool_name
        self.created_at = created_at
        self.input_args = input_args or {}
        self.status = status
        self.is_approved = is_approved


class FakeRepo:
    def __init__(self, existing=None):
        self.calls = {str(c.id): c for c in (existing or [])}

    async def log_tool_call(self, conversation_id, tool_name, input_args, message_id=None, status="pending", requires_approval=False):
        call = FakeToolCall(uuid4(), tool_name, datetime.now(UTC).replace(tzinfo=None), input_args, status=status)
        call.conversation_id = conversation_id
        call.requires_approval = requires_approval
        self.calls[str(call.id)] = call
        return call

    async def get_tool_call(self, tool_call_id, user_id=None):
        return self.calls.get(str(tool_call_id))

    async def claim_tool_call_for_approval(self, tool_call_id, user_id, approved):
        call = self.calls.get(str(tool_call_id))
        if call is None or call.is_approved is not None:
            return None
        call.is_approved = approved
        call.status = "running" if approved else "rejected"
        return call

    async def update_tool_call(self, tool_call_id, status, output_result=None, error_message=None, execution_time_ms=0.0, is_approved=None):
        call = self.calls.get(str(tool_call_id))
        if call:
            call.status = status
            if output_result is not None:
                call.output_result = output_result
        return call


def make_service(repo: FakeRepo) -> ElicitationService:
    return ElicitationService(session=None, repo=repo)


@pytest.mark.asyncio
async def test_park_elicitation_returns_id_and_schema():
    repo = FakeRepo()
    service = make_service(repo)
    schema = {"type": "object", "properties": {"target_email": {"type": "string"}}, "required": ["target_email"]}
    parked = await service.park_elicitation(conversation_id=uuid4(), schema=schema, message="Which email?", title="Confirm target")
    assert parked["status"] == "pending"
    assert parked["elicitation_id"]
    assert parked["schema"] == schema
    stored = repo.calls[parked["elicitation_id"]]
    assert stored.tool_name == ELICITATION_TOOL_NAME
    assert stored.is_approved is None


@pytest.mark.asyncio
async def test_resolve_elicitation_single_use():
    repo = FakeRepo()
    service = make_service(repo)
    parked = await service.park_elicitation(uuid4(), {"required": ["answer"]}, "Do you approve?")
    user_id = uuid4()
    elicitation_id = UUID(parked["elicitation_id"])

    first = await service.resolve_elicitation(elicitation_id, user_id, {"answer": "yes"})
    assert first["resolved"] is True
    assert repo.calls[parked["elicitation_id"]].status == "completed"

    with pytest.raises(ApprovalConsumedError):
        await service.resolve_elicitation(elicitation_id, user_id, {"answer": "no"})


@pytest.mark.asyncio
async def test_resolve_elicitation_unknown_404():
    repo = FakeRepo()
    service = make_service(repo)
    with pytest.raises(ResourceNotFoundError):
        await service.resolve_elicitation(uuid4(), uuid4(), {"answer": "x"})


@pytest.mark.asyncio
async def test_resolve_elicitation_requires_elicitation_kind():
    repo = FakeRepo(existing=[FakeToolCall(uuid4(), "web_search", datetime.now(UTC).replace(tzinfo=None))])
    service = make_service(repo)
    call = next(iter(repo.calls.values()))
    with pytest.raises(ResourceNotFoundError):
        await service.resolve_elicitation(call.id, uuid4(), {"answer": "x"})


@pytest.mark.asyncio
async def test_resolve_expired_elicitation_410():
    repo = FakeRepo()
    service = make_service(repo)
    parked = await service.park_elicitation(uuid4(), {"required": ["x"]}, "msg")
    call = repo.calls[parked["elicitation_id"]]
    call.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1_000_000)
    with pytest.raises(ApprovalExpiredError):
        await service.resolve_elicitation(call.id, uuid4(), {"answer": "x"})