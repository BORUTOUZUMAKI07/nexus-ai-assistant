"""
Task-complexity model router.

Auto-routes each user turn to the appropriate model group (fast_chat vs
complex_reasoning) based on task complexity, with a fallback chain when the
primary provider circuit is open. Keeps cost down by not using heavy models for
simple queries.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.agents.orchestrator.state import AgentState
from backend.app.core.config import settings

# Keywords that suggest complex reasoning is needed.
_COMPLEX_HINTS = [
    "analyze",
    "compare",
    "evaluate",
    "reason",
    "step by step",
    "debug",
    "optimize",
    "architecture",
    "algorithm",
    "proof",
    "derive",
    "calculate",
    "synthesize",
    "review",
    "critique",
    "plan",
    "strategy",
]

# Simple conversational patterns that definitely don't need heavy models.
_SIMPLE_HINTS = [
    "hi",
    "hello",
    "hey",
    "thanks",
    "thank you",
    "bye",
    "good morning",
    "goodbye",
    "yes",
    "no",
    "ok",
    "okay",
]


class ExecutionState(AgentState):
    """The graph state plus the explicit execution-routing decision.

    Declared here rather than in `state.py` because this module owns the
    decision. Both `graph.py` (which builds the graph and routes) and `nodes.py`
    (whose planner consumes the decision) annotate their inputs with it:
    LangGraph filters node and conditional-edge inputs by the type annotation,
    so annotating with plain `AgentState` would silently hide `execution_mode`
    from the very code that has to read it.
    """

    execution_mode: str  # full | simple_chat


@dataclass
class RouteDecision:
    group: str  # "fast_chat" or "complex_reasoning"
    model: str  # specific model name within the group
    reason: str
    estimated_cost_usd: float
    confidence: float  # 0..1 how sure the router is


def _routing_text(content: Any) -> str:
    """Flatten a message's content to text for routing.

    A vision turn's content is OpenAI-style blocks (``[{"type": "text"}, {"type":
    "image_url"}]``), not a string. `route` called ``.strip()`` on it directly, so
    every image turn raised ``'list' object has no attribute 'strip'`` inside the
    router -- before the synthesizer, before any model call -- and the run died
    with the user's turn stored and no answer. Routing only needs the words; the
    blocks keep their shape for the model call that follows.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return " ".join(parts)
    return "" if content is None else str(content)


def _has_complex_hint(text: str) -> bool:
    lower = text.lower()
    return any(h in lower for h in _COMPLEX_HINTS)


def _has_simple_hint(text: str) -> bool:
    lower = text.lower().strip()
    return any(h == lower or lower.startswith(h + " ") for h in _SIMPLE_HINTS)


# The *execution* decision is deliberately separate from the model group.
# `fast_chat` only says which model synthesises the answer; it must never be
# read as permission to skip the agent graph. These are the only modes that
# describe a plain chat turn -- Web/Research, Code, explicit Fast and any
# unknown value keep today's full-graph behaviour.
_SIMPLE_CHAT_MODES = frozenset({"agent", "normal"})

EXECUTION_MODE_FULL = "full"
EXECUTION_MODE_SIMPLE_CHAT = "simple_chat"

# A greeting that ALSO asks for retrieval, a tool, code or a file is not a
# conversational turn. Substring matching is intentionally conservative: a
# false positive merely sends a greeting through the slower full graph, while
# a false negative would skip the retrieval the user explicitly asked for.
_EXPLICIT_INTENT_MARKERS = (
    *_COMPLEX_HINTS,
    "search",
    "web",
    "research",
    "code",
    "tool",
    "fetch",
    "lookup",
    "file",
    "document",
    "attach",
    "image",
    "screenshot",
    "upload",
    "run ",
    "execute",
    "build",
    "write",
    "generate",
    "create",
    "list ",
    "read ",
    "show me",
    "update ",
    "summar",
    "translat",
    "draft",
    "fix",
    "deploy",
    "install",
    "email",
    "url",
    "http",
    "link",
    "download",
    "step",
    "what files",
)


def should_take_simple_chat_fast_path(state: Any) -> bool:
    """Fail-closed decision: is this turn an unambiguous simple chat?

    Every signal has to be known and benign. Anything unknown -- an
    unrecognised mode, a multimodal attachment, a tool/web/code word in the
    text, a stale per-turn tool flag or a non-general task type -- returns
    False so the request keeps the full orchestrator/critic path. Skipping RAG
    on a genuine retrieval request is a correctness bug, so the cost of a
    false positive here is only the extra latency we were trying to remove.
    """
    if state.get("mode") not in _SIMPLE_CHAT_MODES:
        return False
    if state.get("pending_tool_calls") or state.get("needs_web_search"):
        return False
    if state.get("subagent_dispatches"):
        return False
    if state.get("task_type") not in (None, "", "general"):
        return False
    flags = state.get("arq_flags") or {}
    if any(flags.get(key) for key in ("needs_tool", "safety_flag", "recency_needed")):
        return False

    messages = state.get("messages") or []
    if not messages:
        return False
    last = messages[-1]
    role = getattr(last, "type", None) or (last.get("role") if isinstance(last, dict) else None)
    if role not in (None, "human", "user"):
        return False
    content = last.get("content") if isinstance(last, dict) else getattr(last, "content", None)
    # A list is OpenAI-style multimodal content, i.e. an attachment. The model
    # group may still be fast_chat (the text says "hi"), but the execution
    # path must keep the full graph so the image is actually processed.
    if not isinstance(content, str):
        return False
    text = content.strip()
    if not text or not _has_simple_hint(text):
        return False
    lower = text.lower()
    return not any(marker in lower for marker in _EXPLICIT_INTENT_MARKERS)


def should_bypass_agent_graph(state: Any) -> bool:
    """Re-check the routing decision at the point where it is consumed.

    `model_router_node` writes `execution_mode`; the planner short-circuit and
    `route_after_planner` both read it. Re-validating the signals there means a
    stale decision restored from a checkpoint cannot bypass retrieval on a turn
    that actually requested it.
    """
    return (
        state.get("execution_mode") == EXECUTION_MODE_SIMPLE_CHAT
        and should_take_simple_chat_fast_path(state)
    )


def _estimate_cost(tokens: int, group: str) -> float:
    # Pessimistic blended rates per 1k tokens.
    rates = {"fast_chat": 0.0015, "complex_reasoning": 0.006}
    return tokens * rates[group] / 1000.0


def route(user_message: Any, preferred_group: str | None = None) -> RouteDecision:
    """
    Route a user turn to a model group.

    Logic:
      1. If the user explicitly selected a group via the picker, honor it.
      2. If the message matches simple patterns (greetings, short affirmations),
         route to fast_chat with high confidence.
      3. If the message matches complex patterns (analysis, code, math), route
         to complex_reasoning.
      4. Otherwise, fall back to fast_chat (cheaper) with medium confidence --
         the synthesizer/critic can escalate mid-run if needed.
    """
    text = _routing_text(user_message).strip()
    token_estimate = max(1, len(text) // 4)  # rough tokens

    if preferred_group in ("fast_chat", "complex_reasoning"):
        return RouteDecision(
            group=preferred_group,
            model=_default_model(preferred_group),
            reason="user override",
            estimated_cost_usd=_estimate_cost(token_estimate, preferred_group),
            confidence=1.0,
        )

    if _has_simple_hint(text):
        return RouteDecision(
            group="fast_chat",
            model=_default_model("fast_chat"),
            reason="simple conversational pattern",
            estimated_cost_usd=_estimate_cost(token_estimate, "fast_chat"),
            confidence=0.95,
        )

    if _has_complex_hint(text) or len(text) > 400:
        return RouteDecision(
            group="complex_reasoning",
            model=_default_model("complex_reasoning"),
            reason="complex task or long input",
            estimated_cost_usd=_estimate_cost(token_estimate, "complex_reasoning"),
            confidence=0.85,
        )

    # Default: fast_chat, medium confidence. The run can escalate via the
    # critic if the answer quality is too low.
    return RouteDecision(
        group="fast_chat",
        model=_default_model("fast_chat"),
        reason="default route",
        estimated_cost_usd=_estimate_cost(token_estimate, "fast_chat"),
        confidence=0.6,
    )


def _default_model(group: str) -> str:
    if group == "complex_reasoning":
        return settings.COMPLEX_REASONING_MODEL
    return settings.FAST_CHAT_MODEL
