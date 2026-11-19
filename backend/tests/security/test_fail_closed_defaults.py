"""Safety guards must fail closed: an unavailable check may never widen access.

Three guards used to fail open:

* a missing/malformed ``permissions.yaml`` installed a permissive tool list;
* a Redis outage skipped the tool rate limit entirely;
* an ARQ (safety/reasoning constraint check) failure returned all-false flags,
  so the router treated a potentially harmful request as an ordinary ANSWER.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from backend.app.core.exceptions import ToolExecutionError
from backend.app.services.tools import tool_gateway as gateway_module
from backend.app.services.tools.hook_registry import hook_registry
from langchain_core.messages import HumanMessage


def _state(**overrides):
    base = {
        "messages": [HumanMessage(content="What is RAG?")],
        "mode": "normal",
        "user_id": "user-1",
        "task_type": "general",
    }
    base.update(overrides)
    return base


class _RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def warning(self, event: str, **kwargs) -> None:
        self.events.append((event, kwargs))

    def info(self, event: str, **kwargs) -> None:
        self.events.append((event, kwargs))


# ── ARQ: a failed safety check must forbid tools, not permit them ────────────


async def test_arq_provider_failure_is_fail_closed(monkeypatch):
    from backend.app.agents.orchestrator import nodes

    async def _boom(**_kwargs):
        raise RuntimeError("arq provider down")

    monkeypatch.setattr(nodes.ai_client, "completion", _boom)

    flags = await nodes._run_arq("anything")

    assert flags["safety_flag"] is True
    assert flags["needs_tool"] is False


async def test_orchestrator_arq_failure_blocks_dispatches(monkeypatch):
    from backend.app.agents.orchestrator import nodes

    async def _route(*_args, **_kwargs):
        return "RESEARCH"

    async def _boom(_query):
        raise RuntimeError("arq down")

    monkeypatch.setattr(nodes.ai_client, "completion", _route)
    monkeypatch.setattr(nodes, "_run_arq", _boom)

    result = await nodes.orchestrator_node(_state())

    assert result["arq_flags"]["safety_flag"] is True
    assert result["subagent_dispatches"] == []


async def test_orchestrator_cancelled_arq_fails_closed(monkeypatch):
    from backend.app.agents.orchestrator import nodes

    async def _route(*_args, **_kwargs):
        return "CODE"

    async def _cancel(_query):
        raise asyncio.CancelledError

    monkeypatch.setattr(nodes.ai_client, "completion", _route)
    monkeypatch.setattr(nodes, "_run_arq", _cancel)

    result = await nodes.orchestrator_node(_state())

    assert result["arq_flags"]["safety_flag"] is True
    assert result["subagent_dispatches"] == []


async def test_orchestrator_total_route_failure_fails_closed(monkeypatch):
    from backend.app.agents.orchestrator import nodes

    async def _boom(**_kwargs):
        raise RuntimeError("router down")

    monkeypatch.setattr(nodes.ai_client, "completion", _boom)
    monkeypatch.setattr(nodes, "_run_arq", _boom)

    result = await nodes.orchestrator_node(_state())

    assert result["arq_flags"]["safety_flag"] is True
    assert result["subagent_dispatches"] == []


# ── Permissions: no YAML means no automatic tools ────────────────────────────


def test_missing_permissions_file_grants_no_automatic_tools(monkeypatch, tmp_path):
    logger = _RecordingLogger()
    monkeypatch.setattr(gateway_module, "logger", logger)

    gateway = gateway_module.ToolGateway(
        permissions_config_path=str(tmp_path / "does-not-exist.yaml")
    )

    assert gateway.permissions_config == {}
    assert gateway.check_permission("web_search") is False
    assert gateway.requires_approval("web_search", {})[0] is True
    assert any(
        event == "permissions_config_load_failed_fail_closed"
        for event, _ in logger.events
    )


def test_non_mapping_permissions_file_grants_no_automatic_tools(monkeypatch, tmp_path):
    path = tmp_path / "permissions.yaml"
    path.write_text("- web_search\n- calculator\n", encoding="utf-8")
    logger = _RecordingLogger()
    monkeypatch.setattr(gateway_module, "logger", logger)

    gateway = gateway_module.ToolGateway(permissions_config_path=str(path))

    assert gateway.permissions_config == {}
    assert gateway.check_permission("web_search") is False


# ── Rate limit: an unavailable counter may not permit unlimited calls ────────


@pytest.fixture(autouse=True)
def _empty_hook_snapshot():
    hook_registry.set_snapshot([])
    yield
    hook_registry.set_snapshot([])


async def test_rate_limit_outage_blocks_tool_execution(monkeypatch):
    gateway = gateway_module.ToolGateway()

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("redis down")

    dispatched = False

    async def _dispatch(_tool_name, _arguments):
        nonlocal dispatched
        dispatched = True
        return {}

    monkeypatch.setattr(gateway_module.redis_service, "check_rate_limit", _boom)
    monkeypatch.setattr(gateway, "_dispatch", _dispatch)

    with pytest.raises(ToolExecutionError, match="unavailable"):
        await gateway.execute_tool(
            "calculator", {"expression": "2+2"}, user_id=uuid.uuid4(), is_user_approved=True
        )

    assert dispatched is False
