"""Phase-2 Strix findings on the MCP transport.

#14 — the FastMCP sub-application's lifespan must be chained into the parent
app's lifespan. Mounting the sub-app does not start it, so before the fix the
streamable-HTTP session-manager task group never ran and every ``/mcp`` request
answered 500 while the mount looked healthy.

#11 — the MCP transport is a second front door onto tool execution, so it must
apply the *same* revocation / user-existence / active-status rules as the REST
dependency chain, not merely check that the token parses.
"""
from __future__ import annotations

import contextlib
from uuid import uuid4

import backend.app.main as main_module
import httpx
import pytest
from httpx import ASGITransport

# ── infra stubs so the real app lifespan runs without touching services ───────


class _DummyRedis:
    async def ping(self):
        return True

    async def aclose(self):
        return None


class _DummyVector:
    async def ensure_collection(self):
        return None


async def _noop_async(*_a, **_k):
    return None


async def _db_healthy():
    return True


@contextlib.asynccontextmanager
async def _fake_session_factory():
    yield object()


@contextlib.asynccontextmanager
async def _fake_lifespan_graph():
    yield None


def _install_lifespan_stubs(monkeypatch):
    """Neutralise every external dependency the real lifespan touches.

    ``backend/.env`` points at hosted Supabase/Redis/Qdrant, so running the
    genuine lifespan un-stubbed would ``init_db()`` -> ``create_all`` against
    production. Every call that leaves the process is replaced here.
    """
    import backend.app.infrastructure.database.session as session_module

    monkeypatch.setattr(main_module, "init_db", _noop_async)
    monkeypatch.setattr(main_module, "close_db", _noop_async)
    monkeypatch.setattr(main_module, "check_database_health", _db_healthy)
    monkeypatch.setattr(main_module, "redis_client", _DummyRedis())
    monkeypatch.setattr(main_module, "vector_db", _DummyVector())
    monkeypatch.setattr(main_module, "lifespan_graph", _fake_lifespan_graph)
    monkeypatch.setattr(main_module, "shutdown_otel", lambda: None)
    monkeypatch.setattr(session_module, "async_session_factory", _fake_session_factory)
    monkeypatch.setattr(main_module.settings, "MCP_API_KEY", "test-mcp-key")
    monkeypatch.setattr(main_module.settings, "MCP_AUTH_ENABLED", True)


_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "x-nexus-mcp-key": "test-mcp-key",
}

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1.0"},
    },
}


# ── #14 — the sub-app lifespan is chained ─────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_transport_serves_initialize_once_the_lifespan_is_chained(monkeypatch):
    """#14 — with the parent lifespan running, ``/mcp/mcp`` hands out a session.

    Reverting the chain in ``main.lifespan`` (so ``mcp_subapp.lifespan`` is never
    entered) leaves the streamable-HTTP session manager unstarted and this
    request answers 500 instead of 200.
    """
    _install_lifespan_stubs(monkeypatch)
    app = main_module.app
    transport = ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post("/mcp/mcp", json=_INITIALIZE, headers=_MCP_HEADERS)

    assert resp.status_code == 200, resp.text
    assert resp.headers.get("mcp-session-id")


@pytest.mark.asyncio
async def test_mcp_transport_lists_tools_once_the_lifespan_is_chained(monkeypatch):
    """A started transport is not enough — it must actually dispatch a tool call."""
    _install_lifespan_stubs(monkeypatch)
    app = main_module.app
    transport = ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            init = await client.post("/mcp/mcp", json=_INITIALIZE, headers=_MCP_HEADERS)
            assert init.status_code == 200, init.text
            session_id = init.headers.get("mcp-session-id")
            headers = {
                **_MCP_HEADERS,
                "mcp-session-id": session_id,
                "mcp-protocol-version": "2025-06-18",
            }
            await client.post(
                "/mcp/mcp",
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=headers,
            )
            listed = await client.post(
                "/mcp/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                headers=headers,
            )

    assert listed.status_code == 200, listed.text
    assert "create_artifact" in listed.text
    assert "request_user_input" in listed.text


@pytest.mark.asyncio
async def test_health_reports_the_mcp_transport_started_after_lifespan(monkeypatch):
    """#14 — readiness surfaces the transport state so a monitor can alert on it."""
    _install_lifespan_stubs(monkeypatch)
    app = main_module.app
    transport = ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.get("/health/ready")

    assert resp.status_code == 200
    assert resp.json()["mcp_transport"] == "started"


# ── #11 — the MCP gate applies the REST rules ─────────────────────────────────


class _User:
    def __init__(self, active: bool):
        self.is_active = active


class _FakeCache:
    def __init__(self, revoked):
        self._revoked = set(revoked)

    async def is_token_blacklisted(self, jti: str) -> bool:
        return jti in self._revoked


class _Repo:
    def __init__(self, _session):
        pass

    async def get_by_id(self, _uid):
        return _Repo.user


def _wire_mcp_token_check(monkeypatch, *, payload, revoked=(), user):
    import backend.app.api.deps as deps
    import backend.app.infrastructure.database.session as session_module

    monkeypatch.setattr(deps, "decode_token", lambda _t: payload)
    monkeypatch.setattr(deps, "get_cache_service", lambda: _FakeCache(revoked))
    _Repo.user = user
    monkeypatch.setattr(deps, "UserRepository", _Repo)
    monkeypatch.setattr(session_module, "async_session_factory", _fake_session_factory)


@pytest.mark.asyncio
async def test_mcp_rejects_a_token_revoked_on_logout(monkeypatch):
    """#11 — a valid-but-blacklisted access token must not open the MCP door."""
    _wire_mcp_token_check(
        monkeypatch,
        payload={"type": "access", "sub": str(uuid4()), "jti": "revoked-jti"},
        revoked={"revoked-jti"},
        user=_User(active=True),
    )
    assert await main_module._mcp_token_is_valid("tok") is False


@pytest.mark.asyncio
async def test_mcp_rejects_a_token_for_a_deactivated_user(monkeypatch):
    _wire_mcp_token_check(
        monkeypatch,
        payload={"type": "access", "sub": str(uuid4()), "jti": "j-1"},
        user=_User(active=False),
    )
    assert await main_module._mcp_token_is_valid("tok") is False


@pytest.mark.asyncio
async def test_mcp_rejects_a_token_for_a_missing_user(monkeypatch):
    _wire_mcp_token_check(
        monkeypatch,
        payload={"type": "access", "sub": str(uuid4()), "jti": "j-1"},
        user=None,
    )
    assert await main_module._mcp_token_is_valid("tok") is False


@pytest.mark.asyncio
async def test_mcp_rejects_a_refresh_token(monkeypatch):
    _wire_mcp_token_check(
        monkeypatch,
        payload={"type": "refresh", "sub": str(uuid4()), "jti": "j-1"},
        user=_User(active=True),
    )
    assert await main_module._mcp_token_is_valid("tok") is False


@pytest.mark.asyncio
async def test_mcp_accepts_a_valid_active_access_token(monkeypatch):
    """Control: the guard must not reject everything."""
    _wire_mcp_token_check(
        monkeypatch,
        payload={"type": "access", "sub": str(uuid4()), "jti": "j-1"},
        user=_User(active=True),
    )
    assert await main_module._mcp_token_is_valid("tok") is True
