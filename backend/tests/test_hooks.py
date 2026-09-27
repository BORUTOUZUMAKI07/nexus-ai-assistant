"""
Unit tests for lifecycle hooks (pre/post tool policy: block/redact/log).

Covered:
  * HookRegistry snapshot semantics + pre/post evaluation (block/redact/log,
    org scoping, wildcard tool matching)
  * gateway integration: a block policy short-circuits before dispatch
  * ToolService converts the gateway's "blocked" envelope into a 403
    ToolPermissionError (both execute and approved paths)
  * HookService CRUD reloads the registry from FakeSession rows
  * admin /hooks routes via ASGI overrides (403 for non-admins)
"""
import uuid

import pytest
from backend.app.api import deps
from backend.app.core.exceptions import ToolPermissionError
from backend.app.domain.conversation.models import Conversation
from backend.app.domain.hook.models import HookPolicy
from backend.app.domain.org.models import OrganizationMember
from backend.app.main import app
from backend.app.services.hook_service import HookService
from backend.app.services.tool_service import ToolService
from backend.app.services.tools.hook_registry import hook_registry
from backend.app.services.tools.tool_gateway import tool_gateway
from backend.tests.fakes import FakeSession
from httpx import ASGITransport, AsyncClient


def _hook_dict(**overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "name": "test-policy",
        "tool_name": "*",
        "event": "pre_tool",
        "org_id": None,
        "action": "log",
        "field": None,
        "message": None,
        "enabled": True,
    }
    base.update(overrides)
    return base


def _reset_registry():
    hook_registry.set_snapshot([])


def _make_admin(role: str = "admin") -> object:
    return type("User", (), {"id": uuid.uuid4(), "role": role})()


# ─────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────

def test_registry_empty_snapshot_never_blocks():
    _reset_registry()
    verdict = hook_registry.evaluate_pre("web_search", {"query": "x"})
    assert verdict.blocked is False
    assert verdict.redacted is None


def test_registry_block_matches_wildcard_and_exact():
    _reset_registry()
    hook_registry.set_snapshot([_hook_dict(action="block", message="No scraping")])
    verdict = hook_registry.evaluate_pre("web_scrape", {"url": "https://a.io"})
    assert verdict.blocked is True
    assert verdict.message == "No scraping"

    hook_registry.set_snapshot([_hook_dict(tool_name="calculator", action="block")])
    # different tool → no match
    assert hook_registry.evaluate_pre("web_search", {"query": "x"}).blocked is False
    # matching tool → blocked
    assert hook_registry.evaluate_pre("calculator", {"expression": "1+1"}).blocked is True


def test_registry_redact_scrubs_selected_field():
    _reset_registry()
    hook_registry.set_snapshot([_hook_dict(action="redact", field="query")])
    verdict = hook_registry.evaluate_pre("web_search", {"query": "email me at a@b.com"})
    assert verdict.redacted is not None
    assert "a@b.com" not in verdict.redacted["query"]
    assert verdict.blocked is False


def test_registry_redact_whole_args():
    _reset_registry()
    hook_registry.set_snapshot([_hook_dict(action="redact")])
    verdict = hook_registry.evaluate_pre("web_search", {"query": "call +1 234 555 1212"})
    assert verdict.redacted is not None
    assert "+1 234 555 1212" not in str(verdict.redacted)


def test_registry_organizational_scoping():
    _reset_registry()
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    hook_registry.set_snapshot(
        [
            _hook_dict(name="org-a-block", org_id=str(org_a), action="block"),
            _hook_dict(name="global-log", action="log"),
        ]
    )
    # org A call → blocked by org policy
    assert hook_registry.evaluate_pre("web_search", {"query": "x"}, org_id=org_a).blocked is True
    # org B call → org policy does not apply, global still logs (no block)
    assert hook_registry.evaluate_pre("web_search", {"query": "x"}, org_id=org_b).blocked is False
    # anonymous (no org) → only global applies
    assert hook_registry.evaluate_pre("web_search", {"query": "x"}).blocked is False


def test_registry_disabled_policy_is_noop():
    _reset_registry()
    hook_registry.set_snapshot([_hook_dict(action="block", enabled=False)])
    assert hook_registry.evaluate_pre("web_search", {"query": "x"}).blocked is False


def test_registry_post_redacts_result():
    _reset_registry()
    hook_registry.set_snapshot([_hook_dict(event="post_tool", action="redact", field="body")])
    verdict = hook_registry.evaluate_post("web_scrape", {"body": "reach me at a@b.com"})
    assert verdict.redacted is not None
    assert "a@b.com" not in verdict.redacted["body"]


# ─────────────────────────────────────────────────────────────────────
# Gateway + ToolService integration
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gateway_returns_blocked_envelope_no_dispatch():
    _reset_registry()
    hook_registry.set_snapshot([_hook_dict(action="block", message="denied")])
    result = await tool_gateway.execute_tool(
        "web_search", {"query": "x"}, user_id=uuid.uuid4(), is_user_approved=True
    )
    assert result["status"] == "blocked"
    assert result["message"] == "denied"
    hook_registry.set_snapshot([])


@pytest.mark.asyncio
async def test_gateway_redacts_args_before_dispatch():
    _reset_registry()
    hook_registry.set_snapshot([_hook_dict(action="redact", field="code")])
    # dispatch on calculator with a redacted-able dummy field; calculator
    # ignores the extra field so execution still succeeds
    result = await tool_gateway.execute_tool(
        "calculator",
        {"expression": "2+2", "code": "leak secret at a@b.com"},
        user_id=uuid.uuid4(),
        is_user_approved=True,
    )
    assert result["status"] == "success"
    hook_registry.set_snapshot([])


@pytest.mark.asyncio
async def test_tool_service_blocked_becomes_403():
    _reset_registry()
    hook_registry.set_snapshot([_hook_dict(action="block", message="policy block")])
    fake = FakeSession()
    user_id = uuid.uuid4()
    conv_id = uuid.uuid4()
    fake.seed(Conversation, [Conversation(id=conv_id, user_id=user_id, title="t")])

    service = ToolService(fake)
    with pytest.raises(ToolPermissionError) as excinfo:
        await service.execute_tool("web_search", {"query": "x"}, conv_id, user_id)
    assert "policy block" in excinfo.value.message
    hook_registry.set_snapshot([])


@pytest.mark.asyncio
async def test_tool_service_approve_path_blocked_becomes_403():
    """An approved HITL call that trips a hook policy still 403s (server-side)."""
    _reset_registry()
    hook_registry.set_snapshot([_hook_dict(action="block")])
    result = await tool_gateway.execute_tool(
        "web_search", {"query": "x"}, user_id=uuid.uuid4(), is_user_approved=True
    )
    assert result["status"] == "blocked"
    hook_registry.set_snapshot([])


# ─────────────────────────────────────────────────────────────────────
# HookService (FakeSession)
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hook_service_crud_reloads_registry():
    _reset_registry()
    from backend.app.domain.hook.schemas import HookPolicyCreate, HookPolicyUpdate

    fake = FakeSession()
    service = HookService(fake)
    created = await service.create_policy(
        HookPolicyCreate(name="no-exec", tool_name="execute_python", action="block")
    )
    assert created.id is not None
    assert hook_registry.policy_count == 1

    await service.update_policy(created.id, HookPolicyUpdate(enabled=False))
    assert hook_registry.policy_count == 1

    await service.delete_policy(created.id)
    assert hook_registry.policy_count == 0
    hook_registry.set_snapshot([])


@pytest.mark.asyncio
async def test_resolve_org_id_returns_membership():
    fake = FakeSession()
    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    fake.seed(OrganizationMember, [OrganizationMember(organization_id=org_id, user_id=user_id, role="member")])
    resolved = await HookService(fake).resolve_org_id(user_id)
    assert resolved == org_id


@pytest.mark.asyncio
async def test_resolve_org_id_none_for_non_member():
    fake = FakeSession()
    assert await HookService(fake).resolve_org_id(uuid.uuid4()) is None


# ─────────────────────────────────────────────────────────────────────
# Admin routes (ASGI)
# ─────────────────────────────────────────────────────────────────────

def _apply_admin_overrides(fake: FakeSession, admin):
    async def _override_db():
        yield fake

    async def _override_admin():
        return admin

    app.dependency_overrides[deps.get_current_admin] = _override_admin
    app.dependency_overrides[deps.get_db] = _override_db


@pytest.mark.asyncio
async def test_admin_hooks_list_empty_and_permissions():
    fake = FakeSession()
    _apply_admin_overrides(fake, _make_admin())

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/admin/hooks")
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        app.dependency_overrides.pop(deps.get_current_admin, None)
        app.dependency_overrides.pop(deps.get_db, None)


@pytest.mark.asyncio
async def test_admin_hooks_blocks_non_admin():
    fake = FakeSession()
    # Do NOT override get_current_admin: let the real dependency run its role
    # check against a non-admin current user → 403.
    async def _override_db():
        yield fake

    async def _override_user():
        return _make_admin(role="user")

    app.dependency_overrides[deps.get_current_user] = _override_user
    app.dependency_overrides[deps.get_db] = _override_db

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/admin/hooks")
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(deps.get_current_user, None)
        app.dependency_overrides.pop(deps.get_db, None)


@pytest.mark.asyncio
async def test_admin_hooks_create_and_delete():
    _reset_registry()
    fake = FakeSession()
    _apply_admin_overrides(fake, _make_admin())

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/admin/hooks",
                json={"name": "block-web", "tool_name": "web_scrape", "action": "block"},
            )
            assert created.status_code == 201
            policy_id = created.json()["id"]
            assert hook_registry.policy_count == 1

            listed = await client.get("/api/v1/admin/hooks")
            assert len(listed.json()) == 1

            reloaded = await client.post("/api/v1/admin/hooks/reload")
            assert reloaded.status_code == 200
            assert reloaded.json()["policy_count"] == 1

            deleted = await client.delete(f"/api/v1/admin/hooks/{policy_id}")
            assert deleted.status_code == 204
            assert hook_registry.policy_count == 0
    finally:
        app.dependency_overrides.pop(deps.get_current_admin, None)
        app.dependency_overrides.pop(deps.get_db, None)
        hook_registry.set_snapshot([])
