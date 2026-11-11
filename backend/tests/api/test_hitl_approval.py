"""
Human-in-the-loop approval, end to end through the real seams.

The approval card was unreachable for three independent reasons, and the point
of these tests is that none of them can recur silently:

1. ``requires_approval_if`` was declared in permissions.yaml and never
   evaluated, so the permission ladder could only answer "automatic" or
   "always" -- there was no call the graph could flag.
2. The coder ran model-written code itself with ``is_user_approved=True``, so
   even a flagged call would never reach ``tool_node``.
3. ``tool_node`` consulted ``check_permission`` (mode only), which returns True
   for the sandboxed interpreter.

The graph test compiles a one-node graph around the *real* ``tool_node`` with a
real ``interrupt()`` and a ``MemorySaver``, because §9.15: a test that exercises
the piece directly cannot tell you the caller accepts it.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend.app.agents.orchestrator import nodes
from backend.app.agents.orchestrator.state import AgentState
from backend.app.agents.subagents import coder as coder_module
from backend.app.agents.subagents.coder import coder_subagent
from backend.app.services.tools.tool_gateway import tool_gateway
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

RISKY = "import os\nos.system('echo hello')"
NETWORKED = "import requests\nrequests.get('https://example.com')"
BENIGN = "print(sum(range(10)))"


# ── The ladder itself ─────────────────────────────────────────────────────────


def test_requires_approval_truth_table():
    # Benign, sandboxed code is automatic -- the sandbox is the boundary.
    assert tool_gateway.requires_approval("execute_python", {"code": BENIGN}) == (False, None)
    # System commands and network access are the conditional rung.
    risky, risky_reason = tool_gateway.requires_approval("execute_python", {"code": RISKY})
    assert risky is True and "system" in (risky_reason or "")
    net, net_reason = tool_gateway.requires_approval("execute_python", {"code": NETWORKED})
    assert net is True and "network" in (net_reason or "")
    # A declared ceiling over the default 20s also asks.
    slow, slow_reason = tool_gateway.requires_approval(
        "execute_python", {"code": BENIGN, "timeout_seconds": 120}
    )
    assert slow is True and "20 seconds" in (slow_reason or "")
    # An unknown tool keeps the old conservative default.
    unknown, _ = tool_gateway.requires_approval("terminal_command", {})
    assert unknown is True
    # A plainly automatic tool is untouched.
    assert tool_gateway.requires_approval("calculator", {"expression": "2+2"}) == (False, None)


@pytest.mark.asyncio
async def test_execute_tool_refuses_unapproved_flagged_code(monkeypatch):
    """The gateway gate and the card read the same decision."""

    async def _dispatch_should_not_run(*args, **kwargs):
        raise AssertionError("flagged code reached dispatch without approval")

    monkeypatch.setattr(tool_gateway, "_dispatch", _dispatch_should_not_run)

    result = await tool_gateway.execute_tool(
        "execute_python", {"code": RISKY}, user_id=uuid4(), is_user_approved=False
    )
    assert result["status"] == "requires_approval"
    assert result["arguments"] == {"code": RISKY}


# ── The coder must defer, not self-approve ───────────────────────────────────


@pytest.mark.asyncio
async def test_coder_defers_flagged_code_without_executing(monkeypatch):
    async def _completion(*args, **kwargs):
        return f"```python\n{RISKY}\n```"

    executed: list[str] = []

    async def _spy_execute(*args, **kwargs):
        executed.append(kwargs.get("tool_name", "?"))
        return {"status": "success", "result": {"success": True, "stdout": "ran"}}

    monkeypatch.setattr(coder_module, "ai_client", SimpleNamespace(completion=_completion))
    monkeypatch.setattr(coder_module.tool_gateway, "execute_tool", _spy_execute)

    res = await coder_subagent.execute(task_description="echo hello via the shell")

    assert res["status"] == "requires_approval"
    assert "os.system" in res["code"]
    assert res["approval_reason"]
    # The whole point: the sandbox never saw it.
    assert executed == []


# ── The dispatcher turns a deferred coder into a pending approval ────────────


@pytest.mark.asyncio
async def test_dispatcher_queues_flagged_code_for_tool_node(monkeypatch):
    async def _fake_coder(*args, **kwargs):
        return {
            "subagent": "coder",
            "status": "requires_approval",
            "code": RISKY,
            "approval_reason": "system commands",
        }

    monkeypatch.setattr(nodes.coder_subagent, "execute", _fake_coder)

    out = await nodes.subagent_dispatcher_node({
        "subagent_dispatches": ["coder"],
        "messages": [SimpleNamespace(content="echo hello")],
        "mode": "normal",
        "subagent_outputs": {},
        "pending_tool_calls": [],
    })

    pending = out["pending_tool_calls"]
    assert len(pending) == 1
    assert pending[0]["name"] == "execute_python"
    assert "os.system" in pending[0]["arguments"]["code"]


# ── The graph actually suspends and resumes ──────────────────────────────────


# -- The synthesizer must not render deferred code as "executed" ------------
#
# A deferred coder has no `stdout`, and the old block rendered
# ``Stdout: None`` as though the code had run and produced nothing. The gate is
# the coder's status, not stdout truthiness: a completed run with genuinely
# empty output must still show its code. Both directions are tested, because
# the first alone would be satisfied by deleting the block outright.


class _NoCritic:
    async def evaluate(self, **kwargs):
        return {"approved": True, "critique": ""}


def _coder_state(coder_output: dict) -> dict:
    return {
        "messages": [SimpleNamespace(content="echo hello", type="human")],
        "user_id": "",
        "conversation_id": "c1",
        "trace_id": "",
        "mode": "normal",
        "system_prompt": "SYSTEM",
        "task_type": "general",
        "revision_count": 0,
        "citations": [],
        "subagent_outputs": {"coder": coder_output},
    }


async def _synthesize(monkeypatch, coder_output: dict) -> str:
    """Run the synthesizer and return the prompt it handed the model."""
    from backend.app.services.confidence_service import confidence_service

    captured: dict = {}

    async def _completion(**kwargs):
        captured["messages"] = kwargs["messages"]
        return "an answer"

    monkeypatch.setattr(nodes.ai_client, "completion", _completion)
    monkeypatch.setattr(nodes, "critic_subagent", _NoCritic())
    monkeypatch.setattr(
        confidence_service,
        "decide",
        lambda *a, **k: {
            "confidence": 0.95,
            "action": "answer",
            "grounded": True,
            "threshold": 0.6,
        },
    )

    await nodes.synthesizer_node(_coder_state(coder_output))

    return captured["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_a_deferred_coder_is_not_rendered_as_executed_code(monkeypatch):
    prompt = await _synthesize(
        monkeypatch,
        {"status": "requires_approval", "code": RISKY, "approval_reason": "system commands"},
    )

    assert "### Executed Code & Output" not in prompt
    assert "Stdout: None" not in prompt


@pytest.mark.asyncio
async def test_a_completed_coder_shows_its_code_and_stdout(monkeypatch):
    """The control: the block is not simply gone."""
    prompt = await _synthesize(
        monkeypatch, {"status": "success", "code": BENIGN, "stdout": "45"}
    )

    assert "### Executed Code & Output" in prompt
    assert "print(sum(range(10)))" in prompt
    assert "Stdout: 45" in prompt


@pytest.mark.asyncio
async def test_a_completed_coder_with_empty_output_still_shows_its_code(monkeypatch):
    """Empty output is a real result, so the code stays and `Stdout:` does not."""
    prompt = await _synthesize(
        monkeypatch, {"status": "success", "code": BENIGN, "stdout": ""}
    )

    assert "### Executed Code & Output" in prompt
    assert "print(sum(range(10)))" in prompt
    assert "Stdout:" not in prompt


def _approval_graph():
    builder = StateGraph(AgentState)
    builder.add_node("tool_node", nodes.tool_node)
    builder.set_entry_point("tool_node")
    builder.add_edge("tool_node", END)
    return builder.compile(checkpointer=MemorySaver())


def _flagged_state():
    return {
        "pending_tool_calls": [{"name": "execute_python", "arguments": {"code": RISKY}}],
        "tool_results": [],
        "user_id": str(uuid4()),
    }


@pytest.mark.asyncio
async def test_tool_node_interrupts_then_executes_on_approval(monkeypatch):
    executed: list[str] = []

    async def _spy_execute(*, tool_name, arguments, user_id, is_user_approved=False):
        executed.append(tool_name)
        assert is_user_approved is True  # approval is what unlocks execution
        return {"tool_name": tool_name, "status": "success", "result": {"stdout": "hello"}}

    monkeypatch.setattr(nodes.tool_gateway, "execute_tool", _spy_execute)

    graph = _approval_graph()
    config = {"configurable": {"thread_id": str(uuid4())}}

    await graph.ainvoke(_flagged_state(), config)

    snapshot = await graph.aget_state(config)
    assert snapshot.next == ("tool_node",)
    interrupts = snapshot.tasks[0].interrupts
    payload = interrupts[0].value
    assert payload["action"] == "tool_approval"
    assert payload["tool_name"] == "execute_python"
    assert "system" in payload["reason"]
    assert executed == []  # parked, nothing ran

    resumed = await graph.ainvoke(Command(resume={"action": "approve", "data": {}}), config)

    assert executed == ["execute_python"]
    assert resumed["tool_results"][-1]["status"] == "success"


@pytest.mark.asyncio
async def test_tool_node_denies_on_rejection_without_executing(monkeypatch):
    executed: list[str] = []

    async def _spy_execute(*args, **kwargs):
        executed.append("ran")
        return {"status": "success"}

    monkeypatch.setattr(nodes.tool_gateway, "execute_tool", _spy_execute)

    graph = _approval_graph()
    config = {"configurable": {"thread_id": str(uuid4())}}
    await graph.ainvoke(_flagged_state(), config)

    resumed = await graph.ainvoke(
        Command(resume={"action": "reject", "data": {"reason": "too risky"}}), config
    )

    assert executed == []
    denied = resumed["tool_results"][-1]
    assert denied["status"] == "denied"
    assert "too risky" in denied["error"]
