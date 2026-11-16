"""
Thin wrapper around LiteLLM for LLM calls used by the evaluation service.

Routes through the shared ``ai_client`` (resilient LiteLLM Router with
fallback chain, circuit breaker, bulkhead, BYOK per-attempt, and cost
metering) instead of raw ``litellm.acompletion``. This keeps eval on the
free tier and consistent with every other LLM call in the system.
"""
from __future__ import annotations

from typing import Any

from backend.app.infrastructure.ai.litellm_client import ai_client


async def call_llm(
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.0,
    **kwargs: Any,
) -> str:
    """Call an LLM via the shared ai_client and return the text content."""
    # The judge always uses the fast/free tier group; the explicit model arg
    # is accepted for API compatibility but ignored — eval must never route
    # to a paid model.
    res = await ai_client.complete(
        messages=messages,
        model=model or "fast_chat",
        temperature=temperature,
        response_format=None,
        **kwargs,
    )
    return res.get("content", "")
