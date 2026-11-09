"""
Thin wrapper around LiteLLM for LLM calls used by the evaluation service.
"""
from __future__ import annotations

import os
from typing import Any

import litellm


async def call_llm(
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.0,
    **kwargs: Any,
) -> str:
    """Call an LLM via LiteLLM and return the text content of the response."""
    model = model or os.getenv("EVAL_JUDGE_MODEL", "gpt-4o-mini")
    response = await litellm.acompletion(
        model=model,
        messages=messages,
        temperature=temperature,
        **kwargs,
    )
    return response.choices[0].message.content or ""
