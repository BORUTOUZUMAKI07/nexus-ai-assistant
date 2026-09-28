"""
Unit tests for the multi-tenant isolation layer (T-07): per-org rate-limit
keys, org attribution of usage/cost telemetry, and per-org rollup responses —
all against in-memory fakes (no database, no network).
"""
from uuid import uuid4

import pytest
from backend.app.domain.org.models import OrganizationMember
from backend.app.domain.usage.models import UsageLog
from backend.app.domain.usage.repository import UsageRepository
from backend.app.domain.usage.schemas import OrgUsageSummaryResponse, UsageLogCreate
from backend.app.domain.user.models import User
from backend.app.infrastructure.resilience import rate_limit as rl_mod
from backend.app.infrastructure.resilience.rate_limit import (
    org_scoped_key,
    rate_limit,
)
from backend.app.services.org_service import OrganizationService
from backend.app.services.usage_service import UsageService
from fakes import FakeSession
from fastapi import Request, Response


def _await(coro):
    import asyncio

    # pytest-asyncio tears the current loop down after each async test, so once
    # one has run get_event_loop() raises here. Re-establish a loop instead of
    # depending on collection order.
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _user(email="member@example.com") -> User:
    return User(email=email, username=email.split("@")[0])


# ── org resolution ───────────────────────────────────────────────────────────

def test_resolve_org_id_returns_first_membership():
    user_id, org_id = uuid4(), uuid4()
    fake = FakeSession()
    fake.add(OrganizationMember(organization_id=org_id, user_id=user_id, role="member"))
    resolved = _await(OrganizationService(fake).resolve_org_id(user_id))
    assert resolved == org_id


def test_resolve_org_id_none_for_non_member():
    fake = FakeSession()
    assert _await(OrganizationService(fake).resolve_org_id(uuid4())) is None


def test_resolve_org_id_seed_org_flow():
    """The end-to-end org path: create an org (owner auto-enrolled) then
    resolve the owner's org id through the same resolver used by rate limits
    and usage attribution."""
    owner = _user("owner@example.com")
    fake = FakeSession()
    fake.seed(User, [owner])
    org = _await(OrganizationService(fake).create(owner.id, "Acme Corp"))
    resolved = _await(OrganizationService(fake).resolve_org_id(owner.id))
    assert resolved == org.id


# ── per-org rate-limit keys ──────────────────────────────────────────────────

def test_org_scoped_key_org_members_share_org_bucket():
    org_id = uuid4()
    key = org_scoped_key("tool.execute", uuid4(), org_id)
    assert key == f"tool.execute:org:{org_id}"


def test_org_scoped_key_falls_back_to_user_bucket():
    user_id = uuid4()
    key = org_scoped_key("tool.execute", user_id, None)
    assert key == f"tool.execute:user:{user_id}"


class _RecordingLimiter:
    def __init__(self):
        self.calls = []

    async def check_rate_limit(self, identifier, limit=100, window_seconds=60, cost=1):
        self.calls.append((identifier, limit, window_seconds))
        return True, 30


@pytest.mark.asyncio
async def test_rate_limit_org_scope_folds_org_into_key(monkeypatch):
    """org_scope=True routes the request to the org's shared bucket."""
    org_id = uuid4()

    async def fake_org_resolver(session, user_id):
        return org_id

    limiter = _RecordingLimiter()
    monkeypatch.setattr(rl_mod, "redis_service", limiter)
    monkeypatch.setattr(rl_mod, "resolve_org_id_for_user", fake_org_resolver)

    dep = rate_limit("tool.execute", limit=20, window_seconds=60, org_scope=True).dependency
    user = _user()
    request = Request({"type": "http", "method": "POST", "path": "/tools/execute", "headers": []})
    response = Response()

    await dep(request=request, response=response, current_user=user, session=FakeSession())

    assert limiter.calls == [(f"tool.execute:org:{org_id}", 20, 60)]
    # Standard headers still applied on the allowed path.
    assert response.headers.get("X-RateLimit-Limit") == "20"


@pytest.mark.asyncio
async def test_rate_limit_org_scope_falls_back_to_user_key_without_org(monkeypatch):
    async def fake_org_resolver(session, user_id):
        return None

    limiter = _RecordingLimiter()
    monkeypatch.setattr(rl_mod, "redis_service", limiter)
    monkeypatch.setattr(rl_mod, "resolve_org_id_for_user", fake_org_resolver)

    dep = rate_limit("file.upload", limit=30, window_seconds=60, org_scope=True).dependency
    user = _user()
    request = Request({"type": "http", "method": "POST", "path": "/files/upload", "headers": []})
    response = Response()

    await dep(request=request, response=response, current_user=user, session=FakeSession())

    assert limiter.calls[0][0] == f"file.upload:user:{user.id}"


# ── usage/cost org attribution ───────────────────────────────────────────────

def test_log_usage_persists_org_id():
    user_id, org_id = uuid4(), uuid4()
    fake = FakeSession()
    repo = UsageRepository(fake)
    log = _await(
        repo.log_usage(
            UsageLogCreate(
                user_id=user_id,
                org_id=org_id,
                model="llama-3.3-70b-versatile",
                prompt_tokens=10,
                completion_tokens=5,
            )
        )
    )
    rows = fake.rows[UsageLog]
    assert len(rows) == 1
    assert rows[0].org_id == org_id
    assert rows[0].total_tokens == 15
    assert log.total_tokens == 15


def test_log_usage_without_org_keeps_org_id_none():
    fake = FakeSession()
    repo = UsageRepository(fake)
    _await(repo.log_usage(UsageLogCreate(user_id=uuid4(), model="llama-3.3-70b-versatile")))
    assert fake.rows[UsageLog][0].org_id is None


def test_usage_service_org_summary_response_shape():
    """Aggregate math is exercised by integration tests; this pins the response
    wiring (zeros for an empty org) including the cost rollup fields."""
    org_id = uuid4()
    svc = UsageService(FakeSession())
    summary = _await(svc.get_org_summary(org_id, billing_period="2026-09"))

    assert isinstance(summary, OrgUsageSummaryResponse)
    assert summary.organization_id == org_id
    assert summary.billing_period == "2026-09"
    assert summary.total_requests == 0
    assert summary.total_tokens == 0
    assert summary.prompt_tokens == 0
    assert summary.completion_tokens == 0
    assert summary.cached_tokens == 0
    assert summary.usage_cost_usd == 0.0
    assert summary.cost_entries == 0
    assert summary.cost_input_usd == 0.0
    assert summary.cost_output_usd == 0.0
    assert summary.cost_total_usd == 0.0
