"""Tests for MCP tool discovery translation (OpenAI-format function schemas).

The installed ``fastmcp`` exposes ``FastMCP.list_tools()`` (not the legacy
``get_tools``), and each returned ``Tool`` carries ``parameters`` directly as a
JSON-schema dict. These tests pin the manager's translation + fallback contract
so a provider API drift cannot silently degrade to the hardcoded fallback list.
"""
from unittest.mock import AsyncMock

import pytest
from backend.app.mcp.client import mcp_client
from backend.app.mcp.server import mcp


class _FakeTool:
    def __init__(self, name: str, description: str | None, parameters: dict):
        self.name = name
        self.description = description
        self.parameters = parameters


@pytest.mark.asyncio
async def test_list_available_tools_translates_real_fastmcp_tool(monkeypatch):
    tool = _FakeTool(
        name="hello",
        description="Say hello",
        parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    )

    async def _fake_list_tools(*, run_middleware=True):
        return [tool]

    monkeypatch.setattr(mcp, "list_tools", AsyncMock(side_effect=_fake_list_tools))

    tools = await mcp_client.list_available_tools()

    assert len(tools) == 1
    assert tools[0] == {
        "type": "function",
        "function": {
            "name": "hello",
            "description": "Say hello",
            "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        },
    }


@pytest.mark.asyncio
async def test_list_available_tools_falls_back_on_discovery_failure(monkeypatch):
    async def _boom(*, run_middleware=True):
        raise RuntimeError("provider drift")

    monkeypatch.setattr(mcp, "list_tools", AsyncMock(side_effect=_boom))

    tools = await mcp_client.list_available_tools()

    names = {t["function"]["name"] for t in tools}
    assert names == {"web_search", "execute_python_code"}
