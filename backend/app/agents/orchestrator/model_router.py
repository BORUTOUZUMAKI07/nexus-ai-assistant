"""
Task-complexity model router.

Auto-routes each user turn to the appropriate model group (fast_chat vs
complex_reasoning) based on task complexity, with a fallback chain when the
primary provider circuit is open. Keeps cost down by not using heavy models for
simple queries.
"""
from __future__ import annotations

from dataclasses import dataclass

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


@dataclass
class RouteDecision:
    group: str  # "fast_chat" or "complex_reasoning"
    model: str  # specific model name within the group
    reason: str
    estimated_cost_usd: float
    confidence: float  # 0..1 how sure the router is


def _has_complex_hint(text: str) -> bool:
    lower = text.lower()
    return any(h in lower for h in _COMPLEX_HINTS)


def _has_simple_hint(text: str) -> bool:
    lower = text.lower().strip()
    return any(h == lower or lower.startswith(h + " ") for h in _SIMPLE_HINTS)


def _estimate_cost(tokens: int, group: str) -> float:
    # Pessimistic blended rates per 1k tokens.
    rates = {"fast_chat": 0.0015, "complex_reasoning": 0.006}
    return tokens * rates[group] / 1000.0


def route(user_message: str, preferred_group: str | None = None) -> RouteDecision:
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
    text = user_message.strip()
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
        return getattr(settings, "COMPLEX_REASONING_MODEL", "gpt-4o")
    return getattr(settings, "FAST_CHAT_MODEL", "gpt-4o-mini")
