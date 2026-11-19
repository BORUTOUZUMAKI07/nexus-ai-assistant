"""Native LangGraph node-level error handlers.

Official contract: https://docs.langchain.com/oss/python/langgraph/fault-tolerance

LangGraph calls a handler registered via ``add_node(..., error_handler=...)``
only after that node's retry policy and timeout are exhausted. The handler's
``Command.update`` is written to the same channels as the node's normal return
value, but the generated ``__error_handler__<node>`` node has no edges of its
own: without ``Command(goto=...)`` the run simply stops after the handler.
That is measured in ``tests/agents/test_error_handlers.py``.

Only the planner gets a handler. It is the one node whose failure used to cost
the user the entire turn: it is an *optional* reasoning step (short and simple
turns already return ``plan=None``), but it makes a live LLM call, so a provider
outage after retries used to raise out of the graph and leave no answer at all.
The destination is taken from the real ``route_after_planner`` against the
*updated* state, so fast/simple turns still go to the synthesizer and ordinary
turns still go to the orchestrator.
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


__all__ = ["planner_failure_fallback"]
