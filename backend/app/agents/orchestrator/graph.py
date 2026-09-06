"""
LangGraph Workflow Assembly.
Assembles the complete state graph with:
  - AsyncPostgresSaver checkpointer for DURABLE short-term memory
    (survives server restarts, supports horizontal scaling)
  - InMemoryStore for cross-thread long-term memory via mem0
  - HITL interrupt support via LangGraph Command/interrupt pattern
  - Conditional routing between planner → orchestrator → subagents/tools/synthesizer

Reference: https://langchain-ai.github.io/langgraph/how-tos/persistence_postgres/
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

import structlog

try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
except ImportError:
    AsyncPostgresSaver = None

from backend.app.agents.orchestrator.nodes import (
    bootstrap_node,
    critic_grader_node,
    orchestrator_node,
    planner_node,
    subagent_dispatcher_node,
    synthesizer_node,
    tool_node,
)
from backend.app.agents.orchestrator.state import AgentState
from backend.app.agents.orchestrator.tot_node import tree_of_thoughts_node
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.store.memory import InMemoryStore

logger = structlog.get_logger(__name__)

# ─── Module-level compiled graph (initialised at lifespan) ───────────────────
_compiled_graph: Any | None = None
_store: InMemoryStore | None = None


def route_after_orchestrator(
    state: AgentState,
) -> Literal["subagent_dispatcher", "tool_node", "critic_grader"]:
    """
    Conditional routing — decides next node after orchestrator.
    Priority: subagent dispatch > tool calls > RAG quality gate (critic) > synthesis.
    """
    if state.get("subagent_dispatches"):
        return "subagent_dispatcher"
    if state.get("pending_tool_calls"):
        return "tool_node"
    return "critic_grader"


def route_after_critic(state: AgentState) -> Literal["tool_node", "synthesizer"]:
    """
    CRAG conditional routing — 'insufficient'/'unrelated' verdicts (weak or
    missing local RAG grounding) go through the web-search tool before
    synthesis; 'relevant' verdicts synthesize with local citations directly.
    """
    if state.get("needs_web_search") or state.get("pending_tool_calls"):
        return "tool_node"
    return "synthesizer"


def route_after_planner(state: AgentState) -> Literal["tree_of_thoughts", "orchestrator"]:
    """
    Conditional routing — complex multi-step requests take the Tree of Thoughts
    reasoning path, everything else flows through the standard orchestrator.
    """
    if state.get("plan"):
        return "tree_of_thoughts"
    return "orchestrator"


def route_after_subagent(state: AgentState) -> Literal["tool_node", "synthesizer"]:
    """
    Conditional routing — remaining tool calls (e.g. sandboxed code execution)
    run before synthesis; otherwise synthesize directly.
    """
    if state.get("pending_tool_calls"):
        return "tool_node"
    return "synthesizer"


def _build_workflow() -> StateGraph:
    """Assembles the node/edge structure without compiling (no checkpointer yet)."""
    workflow = StateGraph(AgentState)

    # Nodes
    workflow.add_node("bootstrap", bootstrap_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("tree_of_thoughts", tree_of_thoughts_node)
    workflow.add_node("orchestrator", orchestrator_node)
    workflow.add_node("subagent_dispatcher", subagent_dispatcher_node)
    workflow.add_node("tool_node", tool_node)
    workflow.add_node("critic_grader", critic_grader_node)
    workflow.add_node("synthesizer", synthesizer_node)

    # Edges
    workflow.add_edge(START, "bootstrap")
    workflow.add_edge("bootstrap", "planner")
    workflow.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "tree_of_thoughts": "tree_of_thoughts",
            "orchestrator": "orchestrator",
        },
    )
    workflow.add_edge("tree_of_thoughts", END)
    workflow.add_conditional_edges(
        "orchestrator",
        route_after_orchestrator,
        {
            "subagent_dispatcher": "subagent_dispatcher",
            "tool_node": "tool_node",
            "critic_grader": "critic_grader",
        },
    )
    workflow.add_conditional_edges(
        "subagent_dispatcher",
        route_after_subagent,
        {
            "tool_node": "tool_node",
            "synthesizer": "synthesizer",
        },
    )
    workflow.add_conditional_edges(
        "critic_grader",
        route_after_critic,
        {
            "tool_node": "tool_node",
            "synthesizer": "synthesizer",
        },
    )
    workflow.add_edge("tool_node", "synthesizer")
    workflow.add_edge("synthesizer", END)

    return workflow


@asynccontextmanager
async def lifespan_graph() -> AsyncIterator[None]:
    """
    FastAPI lifespan-compatible context manager.
    Opens a single AsyncPostgresSaver connection pool shared across the app.
    Call from main.py lifespan.

    Usage in main.py:
        async with lifespan_graph():
            yield
    """
    global _compiled_graph, _store

    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://nexus:nexus@localhost:5432/nexus_dev",
    )
    # AsyncPostgresSaver requires the synchronous psycopg3 DSN (no +asyncpg prefix)
    pg_dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")

    logger.info("langgraph_checkpointer_initializing", dsn=pg_dsn[:40] + "...")

    if AsyncPostgresSaver is not None:
        try:
            async with AsyncPostgresSaver.from_conn_string(pg_dsn) as checkpointer:
                # Create checkpoint tables if they don't exist yet
                await checkpointer.setup()
                logger.info("langgraph_checkpoint_tables_ready")

                # Cross-thread store for long-term memory (backed by mem0 below the hood)
                _store = InMemoryStore()

                workflow = _build_workflow()
                _compiled_graph = workflow.compile(
                    checkpointer=checkpointer,
                    store=_store,
                )
                logger.info("langgraph_agent_workflow_compiled_with_postgres_checkpointer")

                yield  # App runs here

            logger.info("langgraph_checkpointer_closed")
        except Exception as exc:
            logger.warning(
                "langgraph_postgres_checkpointer_failed_fallback_inmemory",
                error=str(exc),
                hint="Using in-memory MemorySaver checkpointer for this session.",
            )
            _store = InMemoryStore()
            workflow = _build_workflow()
            _compiled_graph = workflow.compile(
                checkpointer=MemorySaver(),
                store=_store,
            )
            logger.info("langgraph_agent_workflow_compiled_with_inmemory_fallback")

            yield
    else:
        logger.info(
            "langgraph_postgres_checkpointer_not_installed_using_inmemory",
            hint="langgraph-checkpoint-postgres not found. Using in-memory MemorySaver.",
        )
        _store = InMemoryStore()
        workflow = _build_workflow()
        _compiled_graph = workflow.compile(
            checkpointer=MemorySaver(),
            store=_store,
        )
        logger.info("langgraph_agent_workflow_compiled_with_inmemory")

        yield

    _compiled_graph = None
    _store = None


def get_graph() -> Any:
    """
    Returns the compiled graph. Raises if called before lifespan initialisation.
    Prefer importing `orchestrator_graph` at module level for convenience.
    """
    if _compiled_graph is None:
        raise RuntimeError(
            "orchestrator_graph not initialised. "
            "Ensure lifespan_graph() is used in FastAPI lifespan."
        )
    return _compiled_graph


def get_store() -> InMemoryStore:
    """Returns the cross-thread LangGraph store."""
    if _store is None:
        raise RuntimeError("Store not initialised.")
    return _store


# Convenience alias — importable before lifespan (will raise only if called)
class _LazyGraph:
    """Proxy that defers access to the compiled graph until after lifespan init."""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_graph(), name)


orchestrator_graph = _LazyGraph()
