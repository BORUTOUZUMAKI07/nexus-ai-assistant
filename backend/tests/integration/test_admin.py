"""Admin endpoints integration tests: user management, audit, health, evaluations."""
import uuid
from unittest.mock import AsyncMock

import backend.app.api.v1.admin as admin_module
import pytest
from backend.app.domain.system.repository import SystemRepository
from backend.app.domain.system.schemas import AuditLogCreate
from backend.app.domain.usage.models import EvaluationLog


@pytest.mark.asyncio
async def test_admin_required_for_user_list(client, user_auth_headers):
    resp = await client.get("/api/v1/admin/users", headers=user_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_lists_users(client, admin_auth_headers, admin_user):
    resp = await client.get("/api/v1/admin/users", headers=admin_auth_headers)
    assert resp.status_code == 200
    emails = [u["email"] for u in resp.json()]
    assert admin_user.email in emails


@pytest.mark.asyncio
async def test_admin_toggle_user_status(client, admin_auth_headers):
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"toggle-{uuid.uuid4().hex[:8]}@example.com",
            "username": f"toggle-{uuid.uuid4().hex[:6]}",
            "password": "StrongPass123!",
        },
    )
    user_id = reg.json()["id"]

    resp = await client.post(
        f"/api/v1/admin/users/{user_id}/toggle-status",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    again = await client.post(
        f"/api/v1/admin/users/{user_id}/toggle-status",
        headers=admin_auth_headers,
    )
    assert again.json()["is_active"] is True


@pytest.mark.asyncio
async def test_admin_cannot_disable_self(client, admin_auth_headers, admin_user):
    resp = await client.post(
        f"/api/v1/admin/users/{admin_user.id}/toggle-status",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_audit_logs(client, admin_auth_headers, db_session, admin_user):
    repo = SystemRepository(db_session)
    await repo.log_audit(
        AuditLogCreate(
            user_id=admin_user.id,
            action="user.login",
            resource_type="user",
            resource_id=str(admin_user.id),
            status="success",
            details={"ip": "127.0.0.1"},
        )
    )

    resp = await client.get("/api/v1/admin/audit-logs", headers=admin_auth_headers)
    assert resp.status_code == 200
    actions = [log["action"] for log in resp.json()]
    assert "user.login" in actions


@pytest.mark.asyncio
async def test_admin_system_status_healthy(
    client, admin_auth_headers, monkeypatch
):
    monkeypatch.setattr(
        admin_module, "check_database_health", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(admin_module.redis_client, "ping", AsyncMock(return_value=True))

    resp = await client.get("/api/v1/admin/system-status", headers=admin_auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["database"] == "connected"
    assert body["redis_cache"] == "connected"


@pytest.mark.asyncio
async def test_admin_system_status_degrades_when_cache_down(
    client, admin_auth_headers, monkeypatch
):
    monkeypatch.setattr(
        admin_module, "check_database_health", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(admin_module.redis_client, "ping", AsyncMock(return_value=False))

    resp = await client.get("/api/v1/admin/system-status", headers=admin_auth_headers)
    assert resp.json()["status"] == "degraded"


@pytest.mark.asyncio
async def test_admin_quality_evaluation_runs(client, admin_auth_headers):
    resp = await client.post(
        "/api/v1/admin/evaluation/quality",
        json={"prompt": "What is 2+2?", "response": "4", "tokens_used": 14, "latency_ms": 200},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["evaluator"] == "quality"
    assert body["metrics"]["is_valid"] is True
    assert body["metrics"]["word_count"] == 1


@pytest.mark.asyncio
async def test_admin_evaluation_forbidden_for_normal_user(client, user_auth_headers):
    resp = await client.post(
        "/api/v1/admin/evaluation/quality",
        json={"response": "yes"},
        headers=user_auth_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/admin/evaluation/traces/trace-a",
        "/api/v1/admin/evaluation/traces/compare?trace_a=trace-a&trace_b=trace-b",
    ],
)
async def test_admin_trace_routes_require_admin(client, user_auth_headers, path):
    """RED: both trace endpoints must be behind the admin dependency."""
    resp = await client.get(path, headers=user_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_trace_routes_read_real_evaluation_logs(
    client, admin_auth_headers, db_session
):
    """RED: traces must come from evaluation_logs, not an unconditional 404."""
    for trace_id, cost in (("trace-a", 0.01), ("trace-b", 0.03)):
        db_session.add(
            EvaluationLog(
                trace_id=trace_id,
                metric_name="cost_usd",
                score=cost,
                passed=True,
                evaluator="heuristic",
                reason=f"cost for {trace_id}",
            )
        )
        db_session.add(
            EvaluationLog(
                trace_id=trace_id,
                metric_name="confidence_score",
                score=0.5 if trace_id == "trace-a" else 0.8,
                passed=True,
                evaluator="heuristic",
            )
        )
    await db_session.commit()

    single = await client.get(
        "/api/v1/admin/evaluation/traces/trace-a", headers=admin_auth_headers
    )
    assert single.status_code == 200
    span = single.json()
    assert span["trace_id"] == "trace-a"
    assert span["cost_usd"] == 0.01
    assert {event["metric"] for event in span["events"]} == {
        "cost_usd",
        "confidence_score",
    }

    # `/traces/compare` must be registered before `/traces/{trace_id}` or the
    # dynamic segment shadows it and the literal path can never be reached.
    compare = await client.get(
        "/api/v1/admin/evaluation/traces/compare?trace_a=trace-a&trace_b=trace-b",
        headers=admin_auth_headers,
    )
    assert compare.status_code == 200
    body = compare.json()
    assert body["delta"]["cost_usd"] == 0.02
    assert body["delta"]["confidence"] == 0.3

    missing = await client.get(
        "/api/v1/admin/evaluation/traces/no-such-trace", headers=admin_auth_headers
    )
    assert missing.status_code == 404
