"""Simple conversational prompts get an explicit *execution* fast path.

`fast_chat` is a model group, not an execution path: picking it only changes
which model synthesises the answer. A "hi" on the default (agent) UI still ran
bootstrap -> model_router -> planner -> orchestrator -> critic_grader -> ...,
and the live trace put ~2.5s of local RAG in the critic alone.

The fix is a separate state field, `execution_mode`, written once by
`model_router_node` and consumed by both halves of the planner short-circuit
(`nodes.planner_node` and `graph.route_after_planner`). The decision is
fail-closed: anything other than an unambiguous greeting/affirmation with no
tool, web, research, code or attachment intent keeps the full graph.

These tests exercise the real router and the real route function; the
graph-level tests execute a compiled graph with instrumented node bodies to
prove the critic is never entered on the simple path (and still is on Web).

Run: `cd backend && uv run pytest tests/agents/test_simple_prompt_fast_path.py`
"""
from __future__ import annotations

import pytest
from backend.app.agents.orchestrator import graph as graph_mod
from backend.app.agents.orchestrator import nodes
from backend.app.agents.orchestrator.graph import (
    _build_workflow,
    model_router_node,
    route_after_planner,
)
from backend.app.agents.orchestrator.model_router import _has_simple_hint
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver


def _state(content="hi", mode="agent", **overrides) -> dict:
    return {"messages": [HumanMessage(content=content)], "mode": mode, "user_id": "", **overrides}


async def _routed(content="hi", mode="agent", **overrides) -> dict:
    """Run the real router and fold its patch into the state."""
    state = _state(content, mode, **overrides)
    return {**state, **await model_router_node(state)}


async def _run_graph(monkeypatch, state: dict) -> list[str]:
    """Compile the real graph with instrumented bodies; return nodes visited.

    The router and planner stay real (that is the path under test); every other
    node is a recorder so "never entered the critic" is observed, not inferred
    from a routing function's return value.
    """
    visited: list[str] = []

    def _spy(name: str, patch: dict | None = None):
        async def _node(_state):
            visited.append(name)
            return patch or {}

        return _node

    for attr, name in (
        ("bootstrap_node", "bootstrap"),
        ("orchestrator_node", "orchestrator"),
        ("tree_of_thoughts_node", "tree_of_thoughts"),
        ("subagent_dispatcher_node", "subagent_dispatcher"),
        ("tool_node", "tool_node"),
        ("critic_grader_node", "critic_grader"),
        ("synthesizer_node", "synthesizer"),
        ("artifact_node", "artifact"),
    ):
        monkeypatch.setattr(graph_mod, attr, _spy(name))

    real_router = graph_mod.model_router_node
    real_planner = graph_mod.planner_node

    async def _router(current):
        visited.append("model_router")
        return await real_router(current)

    async def _planner(current):
        visited.append("planner")
        return await real_planner(current)

    monkeypatch.setattr(graph_mod, "model_router_node", _router)
    monkeypatch.setattr(graph_mod, "planner_node", _planner)

    async def _no_llm(*args, **kwargs):
        raise AssertionError("the simple path must not spend a planning LLM call")

    monkeypatch.setattr(nodes.ai_client, "completion", _no_llm)

    compiled = _build_workflow().compile(checkpointer=MemorySaver())
    await compiled.ainvoke(state, config={"configurable": {"thread_id": "simple-path-test"}})
    return visited


# ── The decision is a new key, not a reuse of the model group ──────────────────


async def test_a_simple_prompt_sets_an_explicit_execution_mode_alongside_the_unchanged_model_group():
    patch = await model_router_node(_state())

    assert patch["execution_mode"] == "simple_chat"
    assert patch["model_group"] == "fast_chat"
    assert patch["model_reason"] == "simple conversational pattern"


async def test_a_non_simple_prompt_defaults_the_execution_mode_to_full():
    routed = await _routed("what is the capital of France")

    assert routed["model_group"] == "fast_chat"
    assert routed["execution_mode"] == "full"


def test_execution_mode_is_declared_on_the_graph_state_schema():
    """A key an undeclared node patch writes can be dropped silently by LangGraph.

    Keep this assertion so the decision cannot become dead state after a
    refactor of the schema wiring.
    """
    channels = next(iter(_build_workflow().schemas.values()))
    assert "execution_mode" in channels


# ── (a) simple prompt on the default UI takes the fast path ────────────────────


async def test_a_simple_prompt_routes_from_the_planner_straight_to_the_synthesizer():
    routed = await _routed("hi", "agent")

    assert routed["execution_mode"] == "simple_chat"
    assert route_after_planner(routed) == "synthesizer"


async def test_a_simple_prompt_never_enters_the_orchestrator_or_critic_grader(monkeypatch):
    visited = await _run_graph(monkeypatch, _state("hi", "agent"))

    assert visited == ["bootstrap", "model_router", "planner", "synthesizer", "artifact"]


async def test_the_planner_skips_its_llm_but_still_loads_long_term_memories(monkeypatch):
    async def _memory(*, query, user_id, limit):
        return "User prefers concise answers.", []

    monkeypatch.setattr(nodes.long_term_memory, "build_memory_context_block_with_ids", _memory)

    routed = await _routed("hi", "agent", user_id="u-1")
    result = await nodes.planner_node(routed)

    assert result["plan"] is None
    assert result["user_memories"] == ["User prefers concise answers."]


# ── (b) explicit Web / Research / Code still take the full graph ───────────────


@pytest.mark.parametrize("mode", ["research", "code"])
async def test_an_explicit_research_or_code_mode_still_takes_the_full_graph(mode):
    routed = await _routed("hi", mode)

    assert routed["execution_mode"] == "full"
    assert route_after_planner(routed) == "orchestrator"


async def test_research_mode_still_visits_the_critic_grader(monkeypatch):
    visited = await _run_graph(monkeypatch, _state("hi", "research"))

    assert "critic_grader" in visited
    assert "orchestrator" in visited


def test_explicit_fast_mode_is_unaffected_by_the_new_decision():
    state = _state("hi", "fast", execution_mode="full")

    assert route_after_planner(state) == "synthesizer"


# ── (c) tool / web / attachment intent keeps the full graph ────────────────────


@pytest.mark.parametrize(
    "content",
    [
        "thanks search the web for the latest news",
        "ok run the code and show me the output",
        "yes fetch the attachment please",
    ],
)
async def test_a_greeting_carrying_tool_or_web_intent_keeps_the_full_graph(content):
    # Precondition: these ARE simple hints, so the bypass only stays off because
    # of the explicit-intent check, not because the greeting failed to match.
    assert _has_simple_hint(content)

    routed = await _routed(content, "agent")

    assert routed["execution_mode"] == "full"
    assert route_after_planner(routed) == "orchestrator"


async def test_an_attached_image_keeps_the_full_graph_even_with_a_greeting():
    blocks = [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    routed = await _routed(blocks, "agent")

    assert routed["model_group"] == "fast_chat"
    assert routed["execution_mode"] == "full"
    assert route_after_planner(routed) == "orchestrator"


# ── (d) a fast_chat model group is not an execution decision ───────────────────


async def test_fast_chat_group_from_the_default_route_does_not_trigger_the_bypass():
    routed = await _routed("what is the capital of France", "agent")

    assert routed["model_group"] == "fast_chat"
    assert routed["execution_mode"] == "full"
    assert route_after_planner(routed) == "orchestrator"


async def test_an_explicit_fast_chat_model_picker_choice_does_not_trigger_the_bypass():
    routed = await _routed("what is the capital of France", "agent", model="fast_chat")

    assert routed["model_group"] == "fast_chat"
    assert routed["model_reason"] == "user override"
    assert routed["execution_mode"] == "full"
    assert route_after_planner(routed) == "orchestrator"


# ── Fail-closed: unknown or ambiguous signals keep today's graph ───────────────


@pytest.mark.parametrize("mode", [None, "", "unrecognized", "deep"])
async def test_an_unknown_mode_fails_closed(mode):
    routed = await _routed("hi", mode)

    assert routed["execution_mode"] == "full"


async def test_an_explicit_pending_tool_call_fails_closed():
    routed = await _routed("hi", "agent", pending_tool_calls=[{"name": "web_search"}])

    assert routed["execution_mode"] == "full"
    assert route_after_planner(routed) == "orchestrator"


async def test_an_ambiguous_non_general_task_type_fails_closed():
    routed = await _routed("hi", "agent", task_type="vision")

    assert routed["execution_mode"] == "full"


async def test_a_safety_arq_flag_from_the_checkpoint_fails_closed():
    routed = await _routed("hi", "agent", arq_flags={"safety_flag": True})

    assert routed["execution_mode"] == "full"
