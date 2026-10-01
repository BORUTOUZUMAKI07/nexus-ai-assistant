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
from backend.app.core.config import settings
from backend.app.core.redaction import redact_dsn
from psycopg import AsyncConnection
from psycopg.rows import dict_row

try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
except ImportError:
    AsyncPostgresSaver = None

from backend.app.agents.orchestrator.artifact_node import artifact_node
from backend.app.agents.orchestrator.nodes import (
    bootstrap_node,
    critic_grader_node,
    orchestrator_node,
    planner_node,
    subagent_dispatcher_node,
    synthesizer_node,
    tool_node,
)
from backend.app.agents.orchestrator.resilient_checkpointer import ResilientPostgresSaver
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
    # After the synthesizer, never before: the artifact body is the finalized
    # post-critique, post-guardrail text. An artifact built from an earlier
    # draft would be a different document from the one the user is reading.
    workflow.add_node("artifact", artifact_node)

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
    workflow.add_edge("tree_of_thoughts", "synthesizer")
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
    workflow.add_edge("synthesizer", "artifact")
    workflow.add_edge("artifact", END)

    return workflow


def _resolve_checkpoint_dsn() -> str:
    """
    Work out which Postgres the LangGraph checkpointer should use.

    Precedence:

    1. ``LANGGRAPH_CHECKPOINT_DSN``, used verbatim. Set this when the
       checkpointer genuinely needs a different database than the app.
    2. ``DATABASE_URL``, converted from the asyncpg form to the sync psycopg3
       DSN that ``AsyncConnection`` requires.

    In case 2 the port is upgraded when it is a transaction-mode pooler port
    (6543 is Supabase's convention, i.e. pgbouncer in transaction mode).

    Why the upgrade: the checkpointer holds ONE connection for the whole process
    because ``AsyncPostgresSaver`` exposes no pool API. A transaction-mode
    pooler is explicitly allowed to close a server-side connection whenever it
    likes, and it does — after which every chat message dies on its first
    checkpoint read with "server closed the connection unexpectedly". Port 5432
    on the same Supabase host is the session-mode endpoint, which keeps a
    connection open for as long as the client holds it, which is exactly this
    consumer's requirement.

    It is logged at WARNING on purpose: silently rewriting a port would be the
    kind of invisible behaviour that is impossible to debug later. Set
    ``LANGGRAPH_CHECKPOINT_DSN`` explicitly to pin it and silence the warning.
    """
    explicit = settings.LANGGRAPH_CHECKPOINT_DSN
    if explicit:
        return explicit.replace("postgresql+asyncpg://", "postgresql://")

    database_url = settings.DATABASE_URL or os.environ.get(
        "DATABASE_URL", "postgresql://nexus:nexus@localhost:5432/nexus_dev"
    )
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")

    if ":6543/" in dsn:
        dsn = dsn.replace(":6543/", ":5432/")
        logger.warning(
            "langgraph_checkpointer_dsn_upgraded_to_session_mode",
            detail=(
                "DATABASE_URL points at a transaction-mode connection pooler "
                "(port 6543). The checkpointer holds a single long-lived "
                "connection, which such a pooler may close at any time, so the "
                "port was rewritten to 5432 (session mode) for this consumer "
                "only. Everything else — the SQLAlchemy engine included — still "
                "uses DATABASE_URL unchanged. Set LANGGRAPH_CHECKPOINT_DSN "
                "explicitly to override."
            ),
        )
    return dsn


@asynccontextmanager
async def lifespan_graph() -> AsyncIterator[None]:
    """
    FastAPI lifespan-compatible context manager.
    Opens the AsyncPostgresSaver checkpointer connection (durable short-term
    memory) shared across the app, and compiles the workflow graph.
    Call from main.py lifespan.

    Note on the installed langgraph-checkpoint-postgres (3.1.x): AsyncPostgresSaver
    is bound to a single psycopg AsyncConnection — it exposes no pool API, so all
    threads share one checkpointer connection (safe: async ops serialise at the
    protocol level; throughput is bounded by that connection).

    That single long-lived connection is wrapped in a ResilientPostgresSaver,
    which replaces it if the server closes it. Without that, one reaped socket
    kills every subsequent stream with "server closed the connection
    unexpectedly" — see resilient_checkpointer.py for the full account.

    Production behaviour is fail-fast: if the Postgres checkpointer is missing or
    cannot connect, startup ABORTS instead of silently degrading to MemorySaver
    (which would reset every thread on restart and break HITL resume).

    Usage in main.py:
        async with lifespan_graph():
            yield
    """
    global _compiled_graph, _store

    pg_dsn = _resolve_checkpoint_dsn()

    logger.info(
        "langgraph_checkpointer_initializing",
        # Redacted, not truncated. dsn[:40] happened to be safe for a Supabase
        # pooler URL only because `postgres.<16-char-ref>` is 25 characters,
        # which lands the cut on the colon before the password; a shorter
        # username or a `+asyncpg` suffix prints the password in full.
        dsn=redact_dsn(pg_dsn),
        pooling="transaction" if ":6543/" in pg_dsn else "session",
    )

    if AsyncPostgresSaver is not None:
        try:
            # A dedicated AsyncConnection (langgraph's from_conn_string helper is
            # unusable with transaction-mode poolers — it hardcodes
            # prepare_threshold=0, colliding with still-live prepared statements
            # on a reused backend session after a hard restart).
            # `prepare_threshold=None` disables server-side PREPARE for poolers.
            checkpointer_conn = await AsyncConnection.connect(
                pg_dsn,
                autocommit=True,
                prepare_threshold=None,
                row_factory=dict_row,
            )
            try:
                # Recreating the saver is the only way to recover: the graph
                # captures the checkpointer object at compile() time and never
                # looks it up again, so the wrapper (not the saver) has to own
                # the connection and swap it in place.
                checkpointer = ResilientPostgresSaver(
                    dsn=pg_dsn,
                    conn=checkpointer_conn,
                    saver_factory=AsyncPostgresSaver,
                )
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
            finally:
                await checkpointer.aclose()
            logger.info("langgraph_checkpointer_closed")
        except Exception as exc:
            if settings.ENVIRONMENT == "production":
                logger.error(
                    "langgraph_checkpointer_unavailable_in_production_refusing_to_degrade",
                    error=str(exc),
                )
                raise RuntimeError(
                    "LangGraph Postgres checkpointer unavailable in production; "
                    "refusing to silently degrade to MemorySaver. Fix DATABASE_URL "
                    "resolution before starting the API."
                ) from exc
            logger.warning(
                "langgraph_postgres_checkpointer_failed_fallback_inmemory",
                error=str(exc),
                hint="Using in-memory MemorySaver checkpointer for this session (non-production only).",
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
        if settings.ENVIRONMENT == "production":
            logger.error("langgraph_postgres_checkpointer_missing_in_production")
            raise RuntimeError(
                "langgraph-checkpoint-postgres is not installed; cannot persist "
                "agent state in production. Install the dependency before starting."
            )
        logger.info(
            "langgraph_postgres_checkpointer_not_installed_using_inmemory",
            hint="langgraph-checkpoint-postgres not found. Using in-memory MemorySaver (non-production only).",
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


# Convenience alias — importable before lifespan (will raise only if called)
class _LazyGraph:
    """Proxy that defers access to the compiled graph until after lifespan init."""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_graph(), name)


orchestrator_graph = _LazyGraph()
