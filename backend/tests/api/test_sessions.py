"""HTTP contract for the active-session inventory endpoints."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from backend.app.api import deps
from backend.app.api.v1 import auth as auth_routes
from backend.app.core.security import create_access_token
from backend.app.domain.user.models import User
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class StubAuthService:
    def __init__(self, rows):
        self.rows = rows
        self.revoked: list[str] = []

    async def list_sessions(self, user_id):
        return self.rows

    async def revoke_session(self, user_id, family_id):
        if family_id == "missing":
            return False
        self.revoked.append(family_id)
        return True


def _row(family_id, agent, created):
    return {
        "family_id": family_id,
        "created_at": created,
        "last_seen": created,
        "expires_at": int((datetime.now(UTC) + timedelta(days=1)).timestamp()),
        "user_agent": agent,
    }


@pytest.fixture
async def api(monkeypatch):
    user = User(id=uuid4(), email="sessions@example.com", username="sessions", is_active=True)
    rows = [
        _row("fam-a", "Laptop", "2026-01-02T00:00:00+00:00"),
        _row("fam-b", "Phone", "2026-01-01T00:00:00+00:00"),
    ]
    stub = StubAuthService(rows)
    app = FastAPI()
    app.include_router(auth_routes.router)
    app.dependency_overrides[deps.get_current_user] = lambda: user
    app.dependency_overrides[deps.get_auth_service] = lambda: stub
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, user, stub


@pytest.mark.asyncio
async def test_list_sessions_marks_the_calling_device(api):
    client, user, _stub = api
    token = create_access_token(
        subject=user.id,
        expires_delta=timedelta(minutes=5),
        additional_claims={"fam": "fam-b"},
    )

    res = await client.get("/auth/sessions", headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 200
    data = res.json()
    assert [row["family_id"] for row in data] == ["fam-a", "fam-b"]
    assert [row["is_current"] for row in data] == [False, True]
    assert data[0]["user_agent"] == "Laptop"


@pytest.mark.asyncio
async def test_revoke_session_returns_204_for_a_known_family(api):
    client, user, stub = api
    token = create_access_token(subject=user.id, expires_delta=timedelta(minutes=5))

    res = await client.delete("/auth/sessions/fam-a", headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 204
    assert stub.revoked == ["fam-a"]


@pytest.mark.asyncio
async def test_revoke_session_returns_404_for_an_unknown_family(api):
    client, user, _stub = api
    token = create_access_token(subject=user.id, expires_delta=timedelta(minutes=5))

    res = await client.delete("/auth/sessions/missing", headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 404
