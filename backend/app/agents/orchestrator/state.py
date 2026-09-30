"""
Agent State Schema for LangGraph Orchestration.
Uses Annotated[Sequence[BaseMessage], add_messages] for standard LangChain message reducer.
"""
from collections.abc import Sequence
from typing import Annotated, Any

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    # Core conversation message stream with LangGraph add_messages reducer
    messages: Annotated[Sequence[BaseMessage], add_messages]

    # Session identifiers
    user_id: str
    conversation_id: str
    trace_id: str
    mode: str  # normal | agent | code | research

    # Context & Personalization
    system_prompt: str
    active_skills: list[str]
    user_memories: list[str]

    # Planning & Task Contracts
    plan: list[str] | None
    current_step: int
    task_type: str  # general, code, research, rag

    # Tool Execution & HITL
    pending_tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    hitl_approved: bool

    # Subagent Coordination
    subagent_dispatches: list[str]
    subagent_outputs: dict[str, Any]

    # Tree of Thoughts & Grounding
    tot_candidates: list[str]
    citations: list[dict[str, Any]]
    evidence_score: float
    evidence_gate_passed: bool | None

    # Self-Refinement (Critic subagent feedback loop)
    revision_count: int
    critique: dict[str, Any] | None

    # Critic / Grader (RAG quality gate) & CRAG routing
    rag_relevance_score: float
    grader_verdict: str  # relevant | insufficient | unrelated
    grader_confidence: float
    needs_web_search: bool

    # Error handling & Recovery
    retry_count: int
    error: str | None

    # ARQ (Attentive Reasoning Queries) — constraint check output from orchestrator
    # Keys: needs_tool, safety_flag, recency_needed, ambiguous — all bool.
    arq_flags: dict[str, bool] | None

    # Run spend accounting (D2). Every loop in the graph is individually
    # bounded, and their product is not, so the run carries a cumulative
    # ceiling. These live in the state rather than only in a contextvar
    # because the graph can suspend for HITL approval and resume in a later
    # request, where in-memory accounting is gone — and a budget that resets on
    # every suspend is a budget that never binds on exactly the long runs it
    # exists to catch.
    loop_steps: int
    tokens_used: int
    spend_stop_reason: str
