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
    mode: str  # normal | agent | code | research | fast

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
    tot_synthesis: str
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

    # Artifact generation (D3). Set by the `artifact` node when the finished
    # turn is a durable document. The stream route reads these off the
    # checkpoint to emit the SSE event, so the frontend learns about the
    # artifact without polling. Absent keys mean "no artifact this turn", which
    # is the normal case and must stay silent.
    artifact_id: str | None
    artifact_title: str
    artifact_version: int
    artifact_created: bool
    artifact_reason: str

    # Model selection (user-facing picker). `model` is the caller's requested
    # synthesis group (`fast_chat` / `complex_reasoning`), whitelisted before it
    # reaches the router. `answer_model` is what actually answered, so the
    model: str | None
    answer_model: str
    model_group: str | None  # fast_chat | complex_reasoning (auto-routed)
    model_confidence: float | None  # router confidence 0..1
    model_reason: str | None  # router rationale
    model_estimated_cost_usd: float | None  # cost estimate for this turn

    # Exact prompt assembly for this turn, written by the synthesizer.
    #
    # The checkpoint already holds the transcript, but not the *assembly*: which
    # memories, research findings and episodic summary were spliced into the
    # system prompt, and which history slice survived compaction. Diagnosing the
    # web-mode refusal took rebuilding a checkpoint blob by hand for exactly this
    # reason. Capped (`truncated` says so) so one pathological prompt cannot
    # bloat every checkpoint for the rest of the thread.
    prompt_snapshot: dict[str, Any] | None
