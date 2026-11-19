"""Native LangGraph node-level error handlers.

Official contract: https://docs.langchain.com/oss/python/langgraph/fault-tolerance

LangGraph calls a handler registered via ``add_node(..., error_handler=...)``
only after that node's retry policy and timeout are exhausted. The handler's
``Command.update`` is written to the same channels as the node's normal return
value, but the generated ``__error_handler__<node>`` node has no edges of its
own: without ``Command(goto=...)`` the run simply stops after the handler.
That is measured in ``tests/agents/test_error_handlers.py``.

Only two nodes get handlers, and both are optional work whose failure used to
cost the user the whole turn:

* ``planner`` is an optional reasoning step (short/simple turns already return
  ``plan=None``) that still makes a live LLM call. Clearing its plan resumes the
  normal ``route_after_planner`` decision.
* ``subagent_dispatcher`` calls researcher/coder, neither of which has an outer
  exception boundary. A scraper or provider failure there used to raise out of
  the graph. Its existing state is preserved and routed with the real
  ``route_after_subagent``.

Every other node either owns its own in-node fallback (ToT, orchestrator,
critic grader, tool node, artifact) or must fail loudly (synthesizer). A handler
there would hide a failure the user needs to hear about.
"""

from __future__ import annotations

from typing import Any

import structlog
from langgraph.errors import NodeError
from langgraph.types import Command

logger = structlog.get_logger(__name__)


def planner_failure_fallback(state: dict[str, Any], error: NodeError) -> Command:
    """Continue a turn whose planner failed after all retries/timeout.

    The parameter is named ``error`` and annotated ``NodeError`` on purpose:
    LangGraph injects the failure context by that exact name and type
    (``langgraph/_internal/_runnable.py``). Renaming it to ``exc`` turns the
    handler into a call with a missing argument at runtime.

    Only the error *type* and node name are logged. Provider exception strings
    can echo request payloads, and this log line is not a place to duplicate
    conversation content.
    """
    logger.error(
        "planner_failed_continuing_without_plan",
        node=error.node,
        error_type=type(error.error).__name__,
    )

    update: dict[str, Any] = {"plan": None, "current_step": 0}
    # Imported here, not at module scope: graph.py imports this module, so a
    # top-level import back into graph.py would be circular. Reusing the real
    # router keeps this failure path from drifting away from the normal one.
    from backend.app.agents.orchestrator.graph import route_after_planner

    return Command(update=update, goto=route_after_planner({**state, **update}))


def subagent_dispatcher_failure_fallback(
    state: dict[str, Any], error: NodeError
) -> Command:
    """Continue a turn whose researcher/coder raised out of the dispatcher.

    Neither subagent has an outer exception boundary, so one provider or scraper
    failure used to raise through this node and cost the user the whole reply.
    The dispatcher's only outputs are ``subagent_outputs`` and
    ``pending_tool_calls``; a node that raised produced neither, so preserving
    what was already in state is the whole recovery. Work queued earlier in the
    turn is explicitly kept -- dropping it would silently skip a paid-for tool
    call. The destination is the real ``route_after_subagent`` on that state.
    """
    logger.error(
        "subagent_dispatcher_failed_continuing_without_new_results",
        node=error.node,
        error_type=type(error.error).__name__,
    )

    update: dict[str, Any] = {
        "subagent_outputs": state.get("subagent_outputs") or {},
        "pending_tool_calls": list(state.get("pending_tool_calls") or []),
    }
    from backend.app.agents.orchestrator.graph import route_after_subagent

    return Command(update=update, goto=route_after_subagent({**state, **update}))


__all__ = ["planner_failure_fallback", "subagent_dispatcher_failure_fallback"]
