"""
Contract tests for the tool-gateway execution envelope.

The gateway is the single producer of the ``execute_tool`` result envelope
consumed by ``ToolService`` (which maps "blocked" -> 403, "requires_approval"
-> 202, "success"/"error" -> passthrough). These tests pin the *exact* key set
of every outcome so the producer/consumer boundary cannot drift silently.

Each outcome is forced without touching the network or a database:
``_dispatch`` is stubbed for the success/error cases, and lifecycle-hook
verdicts come from a seeded registry snapshot (same technique as test_hooks).
"""
import uuid

import pytest
from backend.app.services.tools.hook_registry import hook_registry
from backend.app.services.tools.tool_gateway import tool_gateway

# Contract: documented outcome -> exact envelope keys.
BLOCKED_KEYS = {"status", "tool_name", "message"}
APPROVAL_KEYS = {"status", "tool_name", "arguments", "message"}
SUCCESS_KEYS = {"status", "tool_name", "result", "duration_ms"}
ERROR_KEYS = {"status", "tool_name", "error", "duration_ms"}


def _hook_dict(**overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "name": "contract-policy",
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


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    _reset_registry()


async def _fake_dispatch(tool_name: str, arguments: dict) -> dict:
    return {"result": "ok", "echo": arguments}


@pytest.mark.asyncio
async def test_blocked_pre_envelope_contract(monkeypatch):
    """A pre-tool block short-circuits dispatch and returns the blocked keys."""
    hook_registry.set_snapshot([_hook_dict(action="block", message="denied by policy")])
    dispatched = []

    async def spy_dispatch(tool_name, arguments):
        dispatched.append(tool_name)
        return {}

    monkeypatch.setattr(tool_gateway, "_dispatch", spy_dispatch)

    result = await tool_gateway.execute_tool(
        "web_search", {"query": "x"}, user_id=uuid.uuid4(), is_user_approved=True
    )
    assert result["status"] == "blocked"
    assert set(result) == BLOCKED_KEYS
    assert result["message"] == "denied by policy"
    assert dispatched == []  # blocked envelope must never reach the tool


@pytest.mark.asyncio
async def test_requires_approval_envelope_contract(monkeypatch):
    """Non-approved execution of a non-automatic tool yields the approval keys."""
    monkeypatch.setattr(tool_gateway, "check_permission", lambda tool_name: False)

    result = await tool_gateway.execute_tool(
        "code_execution", {"code": "1+1"}, user_id=uuid.uuid4(), is_user_approved=False
    )
    assert result["status"] == "requires_approval"
    assert set(result) == APPROVAL_KEYS
    assert result["arguments"] == {"code": "1+1"}  # consumer needs the pending args


@pytest.mark.asyncio
async def test_success_envelope_contract(monkeypatch):
    """A clean dispatch returns the success keys with the tool result and latency."""
    monkeypatch.setattr(tool_gateway, "_dispatch", _fake_dispatch)
    _reset_registry()  # no policies → no block/redact

    result = await tool_gateway.execute_tool(
        "calculator", {"expression": "2+2"}, user_id=uuid.uuid4(), is_user_approved=True
    )
    assert result["status"] == "success"
    assert set(result) == SUCCESS_KEYS
    assert result["result"]["result"] == "ok"
    assert result["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_blocked_post_envelope_contract(monkeypatch):
    """A post-tool policy can hide a tool's output after dispatch."""
    monkeypatch.setattr(tool_gateway, "_dispatch", _fake_dispatch)
    hook_registry.set_snapshot([_hook_dict(event="post_tool", action="block", message="output withheld")])

    result = await tool_gateway.execute_tool(
        "calculator", {"expression": "2+2"}, user_id=uuid.uuid4(), is_user_approved=True
    )
    assert result["status"] == "blocked"
    assert set(result) == BLOCKED_KEYS
    assert result["message"] == "output withheld"
    assert "result" not in result  # the tool's output must not leak


@pytest.mark.asyncio
async def test_error_envelope_contract(monkeypatch):
    """A failing dispatch is surfaced as an error envelope, never a hang."""
    async def boom(tool_name, arguments):
        raise RuntimeError("simulated dispatch failure")

    monkeypatch.setattr(tool_gateway, "_dispatch", boom)

    result = await tool_gateway.execute_tool(
        "calculator", {"expression": "2+2"}, user_id=uuid.uuid4(), is_user_approved=True
    )
    assert result["status"] == "error"
    assert set(result) == ERROR_KEYS
    assert "simulated dispatch failure" in result["error"]
