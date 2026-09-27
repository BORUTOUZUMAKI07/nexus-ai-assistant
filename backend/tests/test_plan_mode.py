"""
Unit tests for Plan mode (plan-then-approve).

Covered:
  * planner JSON parsing + heuristic fallback (offline, no LLM)
  * PlanService against the shared in-memory FakeSession
  * the /plan, /plans, approve, reject routes via ASGI dependency overrides
    (IDOR-scoped, consumed-transition 409s included)
"""
import uuid

import pytest
from backend.app.api import deps
from backend.app.core.exceptions import ApprovalConsumedError, ResourceNotFoundError
from backend.app.domain.conversation.models import Conversation
from backend.app.domain.plan.models import Plan
from backend.app.domain.plan.schemas import PlanResponse
from backend.app.main import app
from backend.app.services.conversation_service import ConversationService
from backend.app.services.plan_service import (
    PlanService,
    _heuristic_plan,
    _parse_plan_json,
)
from backend.tests.fakes import FakeSession
from httpx import ASGITransport, AsyncClient


def _make_user() -> object:
    return type("User", (), {"id": uuid.uuid4(), "role": "user", "email": "u@x.io", "username": "u"})()


def _make_conversation(user_id: uuid.UUID) -> Conversation:
    return Conversation(id=uuid.uuid4(), user_id=user_id, title="t", model="m")


def _fake_planner(task, history):
    async def _gen(t, h):
        return "Ship the feature", "Context", ["Step one", "Step two", "Step three"]
    return _gen(task, history)


# ─────────────────────────────────────────────────────────────────────
# Planner parsing + heuristic
# ─────────────────────────────────────────────────────────────────────

def test_parse_plan_json_valid():
    parsed = _parse_plan_json(
        '{"title": "Migrate DB", "summary": "Move to Postgres", '
        '"steps": ["Audit schema", "Write migration", "Verify"]}'
    )
    assert parsed is not None
    title, summary, steps = parsed
    assert title == "Migrate DB"
    assert summary == "Move to Postgres"
    assert steps == ["Audit schema", "Write migration", "Verify"]


def test_parse_plan_json_fenced_and_whitespace():
    parsed = _parse_plan_json('```json\n{"title": "A", "steps": ["x", "y"]}\n```')
    assert parsed is not None
    assert parsed[0] == "A"
    assert parsed[2] == ["x", "y"]


def test_parse_plan_json_invalid_shapes_return_none():
    assert _parse_plan_json("not json at all") is None
    assert _parse_plan_json('{"title": "no steps"}') is None
    assert _parse_plan_json('{"steps": ["x"]}') is None  # no title
    assert _parse_plan_json('{"title": "A", "steps": []}') is None  # empty steps
    assert _parse_plan_json('{"title": "A", "steps": ["ok" for _ in range(20)]}') is None


def test_heuristic_plan_splits_sentences():
    title, summary, steps = _heuristic_plan("Do research. Build a prototype. Test it.")
    assert len(steps) == 3
    assert summary is not None
    assert steps[0].startswith("Do research")
    assert len(title) <= 60


def test_heuristic_plan_single_short_sentence():
    _, _, steps = _heuristic_plan("Just do it.")
    assert steps == ["Just do it."]


# ─────────────────────────────────────────────────────────────────────
# PlanService (FakeSession)
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prepare_plan_creates_pending_plan():
    fake = FakeSession()
    user_id = uuid.uuid4()
    conv = _make_conversation(user_id)
    fake.seed(Conversation, [conv])

    service = PlanService(fake, planner=_fake_planner)
    plan = await service.prepare_plan(conv.id, user_id, "Build the thing")

    assert isinstance(plan, Plan)
    assert plan.status == "pending"
    assert plan.title == "Ship the feature"
    assert plan.steps == ["Step one", "Step two", "Step three"]
    assert plan.user_id == user_id
    assert plan.conversation_id == conv.id
    assert plan.decided_at is None


@pytest.mark.asyncio
async def test_approve_transitions_to_approved():
    fake = FakeSession()
    user_id = uuid.uuid4()
    conv = _make_conversation(user_id)
    plan = Plan(conversation_id=conv.id, user_id=user_id, title="P", steps=["a", "b"])
    fake.seed(Plan, [plan])

    approved = await PlanService(fake).approve(plan.id, user_id)
    assert approved.status == "approved"
    assert approved.decided_at is not None


@pytest.mark.asyncio
async def test_approve_twice_raises_consumed():
    fake = FakeSession()
    user_id = uuid.uuid4()
    conv = _make_conversation(user_id)
    plan = Plan(conversation_id=conv.id, user_id=user_id, title="P", steps=["a"])
    plan.status = "approved"
    fake.seed(Plan, [plan])

    with pytest.raises(ApprovalConsumedError):
        await PlanService(fake).approve(plan.id, user_id)


@pytest.mark.asyncio
async def test_approve_missing_plan_raises_not_found():
    fake = FakeSession()
    with pytest.raises(ResourceNotFoundError):
        await PlanService(fake).approve(uuid.uuid4(), uuid.uuid4())


@pytest.mark.asyncio
async def test_reject_records_reason():
    fake = FakeSession()
    user_id = uuid.uuid4()
    conv = _make_conversation(user_id)
    plan = Plan(conversation_id=conv.id, user_id=user_id, title="P", steps=["a"])
    fake.seed(Plan, [plan])

    rejected = await PlanService(fake).reject(plan.id, user_id, reason="Too risky")
    assert rejected.status == "rejected"
    assert rejected.decision_reason == "Too risky"


@pytest.mark.asyncio
async def test_list_plans_scoped_to_conversation_and_user():
    fake = FakeSession()
    user_id = uuid.uuid4()
    other_user = uuid.uuid4()
    conv_a, conv_b = _make_conversation(user_id), _make_conversation(user_id)
    fake.seed(
        Plan,
        [
            Plan(conversation_id=conv_a.id, user_id=user_id, title="A", steps=["1"]),
            Plan(conversation_id=conv_b.id, user_id=user_id, title="B", steps=["1"]),
            Plan(conversation_id=conv_a.id, user_id=other_user, title="C", steps=["1"]),
        ],
    )
    plans = await PlanService(fake).list_plans(conv_a.id, user_id)
    assert {p.title for p in plans} == {"A"}


# ─────────────────────────────────────────────────────────────────────
# API routes (ASGI overrides)
# ─────────────────────────────────────────────────────────────────────

def _apply_overrides(fake: FakeSession, user, planner=None):
    async def _override_db():
        yield fake

    async def _override_user():
        return user

    async def _plan_service():
        return PlanService(fake, planner=planner or _fake_planner)

    async def _conv_service():
        return ConversationService(fake)

    app.dependency_overrides[deps.get_current_user] = _override_user
    app.dependency_overrides[deps.get_db] = _override_db
    app.dependency_overrides[deps.get_plan_service] = _plan_service
    app.dependency_overrides[deps.get_conversation_service] = _conv_service


async def _clear_overrides(*keys):
    for key in keys:
        app.dependency_overrides.pop(key, None)


@pytest.mark.asyncio
async def test_create_plan_returns_201_pending():
    user = _make_user()
    conv = _make_conversation(user.id)
    fake = FakeSession()
    fake.seed(Conversation, [conv])
    _apply_overrides(fake, user)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/conversations/{conv.id}/plan", json={"task": "Build the thing"}
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"
        assert data["steps"] == ["Step one", "Step two", "Step three"]
        assert data["conversation_id"] == str(conv.id)
    finally:
        await _clear_overrides(deps.get_current_user, deps.get_db, deps.get_plan_service, deps.get_conversation_service)


@pytest.mark.asyncio
async def test_create_plan_for_foreign_conversation_404():
    user = _make_user()
    stranger_conv = _make_conversation(uuid.uuid4())
    fake = FakeSession()
    fake.seed(Conversation, [stranger_conv])
    _apply_overrides(fake, user)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/conversations/{stranger_conv.id}/plan", json={"task": "x"}
            )
        assert resp.status_code == 404
    finally:
        await _clear_overrides(deps.get_current_user, deps.get_db, deps.get_plan_service, deps.get_conversation_service)


@pytest.mark.asyncio
async def test_approve_reject_flow_and_idor():
    user = _make_user()
    conv = _make_conversation(user.id)
    fake = FakeSession()
    fake.seed(Conversation, [conv])
    _apply_overrides(fake, user)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                f"/api/v1/conversations/{conv.id}/plan", json={"task": "Task"}
            )
            plan_id = created.json()["id"]

            approved = await client.post(f"/api/v1/plans/{plan_id}/approve")
            assert approved.status_code == 200
            assert approved.json()["status"] == "approved"

            # second approval → 409 (already consumed)
            again = await client.post(f"/api/v1/plans/{plan_id}/approve")
            assert again.status_code == 409

            # reject an already-approved plan → 409 too
            rejected = await client.post(f"/api/v1/plans/{plan_id}/reject", json={"reason": "nope"})
            assert rejected.status_code == 409

            # a stranger cannot approve the plan (IDOR → 404)
            stranger = _make_user()
            async def _override_stranger():
                return stranger
            app.dependency_overrides[deps.get_current_user] = _override_stranger
            foreign = await client.post(f"/api/v1/plans/{plan_id}/approve")
            assert foreign.status_code == 404
    finally:
        await _clear_overrides(deps.get_current_user, deps.get_db, deps.get_plan_service, deps.get_conversation_service)


@pytest.mark.asyncio
async def test_list_plans_endpoint():
    user = _make_user()
    conv = _make_conversation(user.id)
    fake = FakeSession()
    fake.seed(Conversation, [conv])
    fake.seed(
        Plan,
        [
            Plan(conversation_id=conv.id, user_id=user.id, title="One", steps=["a"]),
            Plan(conversation_id=conv.id, user_id=user.id, title="Two", steps=["b"], status="rejected"),
        ],
    )
    _apply_overrides(fake, user)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/v1/conversations/{conv.id}/plans")
        assert resp.status_code == 200
        titles = [p["title"] for p in resp.json()]
        assert set(titles) == {"One", "Two"}
    finally:
        await _clear_overrides(deps.get_current_user, deps.get_db, deps.get_plan_service, deps.get_conversation_service)


# Pydantic response round-trip sanity check
def test_plan_response_serialization():
    plan = Plan(
        conversation_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        title="P",
        summary="s",
        steps=["a", "b"],
    )
    as_dict = PlanResponse.model_validate(plan).model_dump()
    assert as_dict["status"] == "pending"
    assert as_dict["steps"] == ["a", "b"]


# ─────────────────────────────────────────────────────────────────────
# Plan mode execution preamble (bootstrap_node threading)
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bootstrap_node_appends_plan_preamble_to_system_prompt(monkeypatch):
    """An approved plan's preamble is prepended to the agent system prompt."""
    from backend.app.agents.orchestrator import nodes

    monkeypatch.setattr(
        nodes,
        "get_config",
        lambda: {
            "configurable": {
                "user_id": "u1",
                "thread_id": "c1",
                "mode": "agent",
                "plan_preamble": "APPROVED PLAN — execute:\n1. Do X\n2. Verify\nDo not deviate.",
            }
        },
    )
    r = await nodes.bootstrap_node({})
    assert r["system_prompt"].startswith("You are Nexus AI")
    assert r["system_prompt"].endswith(
        "APPROVED PLAN — execute:\n1. Do X\n2. Verify\nDo not deviate."
    )


@pytest.mark.asyncio
async def test_bootstrap_node_without_preamble_keeps_default_prompt(monkeypatch):
    """Without a plan_preamble the system prompt is untouched."""
    from backend.app.agents.orchestrator import nodes

    monkeypatch.setattr(
        nodes,
        "get_config",
        lambda: {"configurable": {"user_id": "u1", "thread_id": "c1", "mode": "normal"}},
    )
    r = await nodes.bootstrap_node({})
    assert r["system_prompt"].startswith("You are Nexus AI")
    assert "APPROVED PLAN" not in r["system_prompt"]


def test_stream_request_accepts_plan_preamble():
    """The stream endpoint schema carries the approved-plan preamble."""
    from backend.app.api.v1.conversations import StreamChatRequest

    req = StreamChatRequest(
        messages=[{"role": "user", "content": "hi"}],
        mode="agent",
        plan_preamble="APPROVED PLAN — Step one",
    )
    assert req.plan_preamble == "APPROVED PLAN — Step one"
    assert req.plan_preamble is not None
