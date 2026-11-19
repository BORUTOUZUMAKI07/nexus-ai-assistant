"""Native LangGraph node-level error handling for the orchestrator.

Official contract: https://docs.langchain.com/oss/python/langgraph/fault-tolerance

Two facts are proven here, not inferred from reading `graph.py`:

1. The compiled graph really carries a LangGraph `error_handler` on `planner`
   (LangGraph turns that kwarg into an internal `__error_handler__planner` node).
   A module full of correct handlers that nothing passes to `add_node` is
   indistinguishable from one with no handlers at all from the outside.
2. When the planner raises, the handler's state update is applied *before* the
   conditional edge runs, so the turn continues down the real routing path
   instead of losing the user's answer to a graph-level exception.

The handler is deliberately not a `Command(goto=...)`: the existing
`route_after_planner` already distinguishes fast/simple turns from full turns.
Forcing a fixed destination would silently change fast-mode behaviour on the
one path where the planner is least likely to have completed.
"""

from __future__ import annotations

from typing import TypedDict

from backend.app.agents.orchestrator.graph import (
    _build_workflow,
    route_after_planner,
    route_after_subagent,
)
from backend.app.agents.orchestrator.retry_policy import TIMEOUTS
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph


class _State(TypedDict):
    plan: list[str] | None
    current_step: int
    reached: str
    mode: str


class _DispatchState(TypedDict):
    subagent_outputs: dict
    pending_tool_calls: list
    reached: str


#: The only nodes whose failure is survivable without inventing new evidence.
#: Every other node either owns its own fallback or must fail loudly.
_NATIVE_HANDLER_NODES = {"planner", "subagent_dispatcher"}


def test_compiled_graph_attaches_native_error_handlers_only_where_intended() -> None:
    compiled = _build_workflow().compile()

    for name in _NATIVE_HANDLER_NODES:
        spec = compiled.nodes[name]
        assert spec.error_handler_node, f"{name} has no native error handler"
        assert compiled.nodes[spec.error_handler_node].is_error_handler

    for name, spec in compiled.nodes.items():
        if spec.is_error_handler or name == "__start__":
            continue
        assert name in _NATIVE_HANDLER_NODES or spec.error_handler_node is None, (
            f"{name} unexpectedly has a node-level error handler"
        )


def test_policy_registration_contract_ignores_langgraph_internal_nodes() -> None:
    """LangGraph adds `__error_handler__planner` to the *builder* node set.

    The policy table must still cover every real node, both directions. Internal
    nodes are excluded by name prefix rather than by dropping the assertion.
    """
    registered = set(_build_workflow().nodes)
    real_nodes = {n for n in registered if not n.startswith("__error_handler__")}
    assert set(TIMEOUTS) == real_nodes


async def test_failing_planner_recovers_through_the_real_handler() -> None:
    from backend.app.agents.orchestrator.error_handlers import planner_failure_fallback

    def boom(_: _State) -> dict:
        raise RuntimeError("planner provider unavailable")

    def tot(state: _State) -> dict:
        return {"reached": "tot"}

    def orchestrator(state: _State) -> dict:
        return {"reached": "orchestrator"}

    workflow = StateGraph(_State)
    workflow.add_node("planner", boom, error_handler=planner_failure_fallback)
    workflow.add_node("tot", tot)
    workflow.add_node("orchestrator", orchestrator)
    workflow.add_edge(START, "planner")
    workflow.add_conditional_edges(
        "planner",
        lambda state: "tot" if state.get("plan") else "orchestrator",
        {"tot": "tot", "orchestrator": "orchestrator"},
    )
    workflow.add_edge("tot", END)
    workflow.add_edge("orchestrator", END)
    graph = workflow.compile(checkpointer=InMemorySaver())

    # A stale plan is present on purpose: if the handler's update were not
    # applied before the conditional edge, routing would pick `tot` and the
    # assertion below would fail.
    result = await graph.ainvoke(
        {
            "plan": ["stale plan from a previous decision"],
            "current_step": 3,
            "reached": "",
            "mode": "normal",
        },
        config={"configurable": {"thread_id": "planner-recovery"}},
    )

    assert result["plan"] is None
    assert result["current_step"] == 0
    assert result["reached"] == "orchestrator"


def test_handler_uses_the_real_route_on_the_updated_state() -> None:
    """The handler must not hardcode a destination or route on the stale plan.

    With a stale plan the normal router would pick `tree_of_thoughts`; after the
    handler clears it, an ordinary turn goes to the orchestrator and a fast turn
    still goes to the synthesizer.
    """
    from backend.app.agents.orchestrator.error_handlers import planner_failure_fallback
    from langgraph.errors import NodeError

    error = NodeError("planner", RuntimeError("provider unavailable"))
    base: dict = {"mode": "normal", "plan": ["stale"], "current_step": 3}

    for override, expected in (
        ({}, "orchestrator"),
        ({"mode": "fast"}, "synthesizer"),
    ):
        state = {**base, **override}
        command = planner_failure_fallback(state, error)
        assert command.update == {"plan": None, "current_step": 0}
        assert command.goto == expected
        assert command.goto == route_after_planner({**state, **command.update})
        assert command.goto != "tree_of_thoughts"


async def test_failing_subagent_dispatcher_recovers_without_losing_pending_work() -> None:
    from backend.app.agents.orchestrator.error_handlers import (
        subagent_dispatcher_failure_fallback,
    )

    def boom(_: _DispatchState) -> dict:
        raise RuntimeError("researcher provider unavailable")

    def tool_node(state: _DispatchState) -> dict:
        return {"reached": "tool_node"}

    def synthesizer(state: _DispatchState) -> dict:
        return {"reached": "synthesizer"}

    workflow = StateGraph(_DispatchState)
    workflow.add_node(
        "subagent_dispatcher",
        boom,
        error_handler=subagent_dispatcher_failure_fallback,
    )
    workflow.add_node("tool_node", tool_node)
    workflow.add_node("synthesizer", synthesizer)
    workflow.add_edge(START, "subagent_dispatcher")
    workflow.add_conditional_edges(
        "subagent_dispatcher",
        route_after_subagent,
        {"tool_node": "tool_node", "synthesizer": "synthesizer"},
    )
    workflow.add_edge("tool_node", END)
    workflow.add_edge("synthesizer", END)
    graph = workflow.compile(checkpointer=InMemorySaver())

    # A queued call from earlier in the turn must survive the failed dispatch:
    # dropping it would silently skip work the user already paid for.
    result = await graph.ainvoke(
        {
            "subagent_outputs": {"prior": {"status": "ok"}},
            "pending_tool_calls": [{"name": "web_search", "arguments": {"query": "q"}}],
            "reached": "",
        },
        config={"configurable": {"thread_id": "dispatcher-recovery"}},
    )
    assert result["subagent_outputs"] == {"prior": {"status": "ok"}}
    assert result["pending_tool_calls"] == [
        {"name": "web_search", "arguments": {"query": "q"}}
    ]
    assert result["reached"] == "tool_node"


def test_dispatcher_handler_uses_the_real_route_on_the_updated_state() -> None:
    from backend.app.agents.orchestrator.error_handlers import (
        subagent_dispatcher_failure_fallback,
    )
    from langgraph.errors import NodeError

    error = NodeError("subagent_dispatcher", RuntimeError("coder unavailable"))

    for pending, expected in (([], "synthesizer"), ([{"name": "web_search"}], "tool_node")):
        state = {
            "subagent_outputs": {"prior": {"status": "ok"}},
            "pending_tool_calls": pending,
        }
        command = subagent_dispatcher_failure_fallback(state, error)
        assert command.update == {
            "subagent_outputs": {"prior": {"status": "ok"}},
            "pending_tool_calls": pending,
        }
        assert command.goto == expected
        assert command.goto == route_after_subagent({**state, **command.update})
