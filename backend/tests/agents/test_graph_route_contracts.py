"""Conditional-edge contracts must not advertise destinations the router
cannot return (an unreachable edge is configuration that reads as a safety
net but is not one).
"""
from __future__ import annotations

from typing import get_args, get_type_hints

from backend.app.agents.orchestrator import graph
from langchain_core.messages import HumanMessage


def test_orchestrator_router_has_no_unreachable_synthesizer_destination():
    return_hint = get_type_hints(graph.route_after_orchestrator)["return"]
    assert set(get_args(return_hint)) == {
        "subagent_dispatcher",
        "tool_node",
        "critic_grader",
    }


def test_orchestrator_router_returns_only_declared_destinations():
    base = {"messages": [HumanMessage(content="hello")]}
    states = [
        base,
        {**base, "arq_flags": {"safety_flag": True}},
        {**base, "subagent_dispatches": ["researcher"]},
        {**base, "pending_tool_calls": [{"tool": "calculator"}]},
    ]
    allowed = set(get_args(get_type_hints(graph.route_after_orchestrator)["return"]))

    for state in states:
        assert graph.route_after_orchestrator(state) in allowed


def test_safety_flag_wins_over_subagent_and_tool_dispatches():
    state = {
        "messages": [HumanMessage(content="hello")],
        "arq_flags": {"safety_flag": True},
        "subagent_dispatches": ["researcher"],
        "pending_tool_calls": [{"tool": "web_search"}],
    }
    assert graph.route_after_orchestrator(state) == "critic_grader"
    assert graph.route_after_critic(state) == "synthesizer"
