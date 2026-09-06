"""E2E-tier fixture package.

Boots the real FastAPI app on a live uvicorn socket and exercises it over real
HTTP (no ASGI in-process transport).  The database is a dedicated ``nexus_e2e``
database inside a real PostgreSQL testcontainer; external providers (LLM, MCP,
tool sandbox) are stubbed autouse so the honest network + server + DB lifecycle
is what runs.
"""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.infrastructure.ai.litellm_client import ai_client  # noqa: E402
from backend.app.mcp.client import mcp_client  # noqa: E402
from backend.app.services.tools.tool_gateway import tool_gateway  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))


def pytest_collection_modifyitems(items):
    """pytest 9 does not apply conftest ``pytestmark`` to collected items.
    Only tag tests living under this directory, so the whole-suite hook does
    not accidentally deselect other tiers."""
    root = Path(__file__).resolve().parent
    for item in items:
        if root in Path(str(item.path)).parents:
            item.add_marker(pytest.mark.e2e)


FAKE_COMPLETION = "This is a deterministic assistant reply for the e2e suite."

FAKE_TOOL_DICT = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for up to date information.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}


@pytest.fixture(autouse=True)
def _stub_ai_client(monkeypatch):
    """Deterministic LLM completions; never hits a provider."""

    async def _completion(*, messages, model="llama-3.3-70b-versatile", temperature=0.7, max_tokens=4096):
        return FAKE_COMPLETION

    async def _stream_completion(*, messages, model="llama-3.3-70b-versatile", temperature=0.7, max_tokens=4096):
        for token in FAKE_COMPLETION.split(" "):
            yield token + " "

    monkeypatch.setattr(ai_client, "completion", _completion)
    monkeypatch.setattr(ai_client, "stream_completion", _stream_completion)


@pytest.fixture(autouse=True)
def _stub_mcp_discovery(monkeypatch):
    """Return a fixed tool list without touching FastMCP server discovery."""

    async def _list():
        return [FAKE_TOOL_DICT]

    monkeypatch.setattr(mcp_client, "list_available_tools", _list)


@pytest.fixture(autouse=True)
def _stub_tool_gateway(monkeypatch):
    """Canned tool execution result (no network/sandbox)."""

    async def _execute(*, tool_name, arguments, user_id, is_user_approved=False):
        return {
            "status": "completed",
            "tool_name": tool_name,
            "result": {"summary": f"Executed {tool_name}"},
            "duration_ms": 5.0,
        }

    monkeypatch.setattr(tool_gateway, "execute_tool", _execute)


@pytest.fixture(scope="session")
def live_server():
    """Start the app on a live socket once per session; stop it afterwards.

    Uses a dedicated ``nexus_e2e`` database inside a PostgreSQL testcontainer
    so the live tier stays isolated from the ``nexus_test`` integration DB.
    """
    from _testcontainers import get_postgres_url

    database_url = get_postgres_url("nexus_e2e")
    from _serve import start_live_server

    handle = start_live_server(database_url)
    yield handle
    handle.stop()


@pytest.fixture
async def server_client(live_server):
    """Real-TCP httpx client pointed at the live server."""
    async with httpx.AsyncClient(base_url=live_server.base_url, timeout=30.0) as ac:
        yield ac


@pytest.fixture
async def e2e_user(server_client):
    """Register a fresh user over HTTP and return their auth headers."""
    import uuid

    email = f"e2e-{uuid.uuid4().hex[:10]}@example.com"
    password = "TestPass123!"
    resp = await server_client.post(
        "/api/v1/auth/register",
        json={"email": email, "username": f"e2e-{uuid.uuid4().hex[:8]}", "password": password},
    )
    assert resp.status_code == 201
    login = await server_client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    return {
        "email": email,
        "password": password,
        "headers": {"Authorization": f"Bearer {token}"},
    }
