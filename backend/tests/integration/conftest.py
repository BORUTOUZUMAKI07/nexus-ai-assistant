"""Integration-tier fixture package.

Every test in this directory runs against a real PostgreSQL testcontainer
(injected via the root ``override_get_db`` / ``client`` fixtures) exercising
the real repositories, services, and route handlers.  External infrastructure
(Redis, Qdrant, storage, LLM providers) is faked at the specific test/mock
sites where routes touch it.
"""
"""
Integration-tier tests.

Every test here runs against a real PostgreSQL testcontainer
(see the root ``conftest``) and exercises the real repositories, services,
and route handlers through an ASGI httpx client.  External infrastructure
(Redis, Qdrant, cloud storage, LLM providers, FastMCP) is stubbed autouse so
the honest DB/HTTP paths are what actually run.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.infrastructure.ai.litellm_client import ai_client  # noqa: E402
from backend.app.mcp.client import mcp_client  # noqa: E402
from backend.app.services.tools.tool_gateway import tool_gateway  # noqa: E402

pytestmark = pytest.mark.integration


def pytest_collection_modifyitems(items):
    """In pytest 9, ``pytestmark`` in a conftest is not applied to collected
    items, so tag every test collected under this directory with the
    ``integration`` marker explicitly."""
    root = Path(__file__).resolve().parent
    for item in items:
        if root in Path(str(item.path)).parents:
            item.add_marker(pytest.mark.integration)


FAKE_COMPLETION = "This is a deterministic assistant reply for the integration suite."

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
    """Deterministic LLM completions so message routes never hit a provider."""

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
    """Return a canned success result for tool execution (no network/sandbox)."""

    async def _execute(*, tool_name, arguments, user_id, is_user_approved=False):
        return {
            "status": "completed",
            "tool_name": tool_name,
            "result": {"summary": f"Executed {tool_name}"},
            "duration_ms": 5.0,
        }

    monkeypatch.setattr(tool_gateway, "execute_tool", _execute)
