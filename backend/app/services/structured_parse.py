"""
Robust extraction and validation of structured data from LLM text output.

The production failure this closes: the code that turned a model's reply into
JSON was a bare ``json.loads`` after ``raw.strip().strip("```json")``. That
path only works for a perfectly-formed object with nothing around it. A model
that prepends "Here's the JSON:", appends an explanation, wraps the object in a
```json fence with trailing prose, or nests braces inside a quoted string
defeats it -- and because every one of these callers fails *open*, the result is
a silently dropped routing flag or research sub-query, never an error anyone
sees. (The same class of bug as the artifact-frame double-encode recorded in
AGENTS.md §9.27: right up to the parse, wrong at the seam.)

Two layers, and both are needed:

1. **Provider enforcement** -- pass ``response_format={"type": "json_object"}``
   at the call site. The router here spans Groq, Gemini and OpenRouter, and
   support for JSON mode is not uniform, so the request is best-effort and may
   be dropped; that is exactly why layer 2 exists and is not optional.
2. **Defensive parsing** -- :func:`extract_json_object` /
   :func:`extract_json_array` below: fence-aware, prose-tolerant, and using a
   string-aware balanced scan so a brace inside a quoted value cannot truncate
   the object.

The Pydantic validators at the bottom are the last step. Shape is not
correctness: ``bool("false")`` is ``True``, so coercing an ARQ flag with a raw
``bool()`` silently flips a "no" into a "yes". A validating model rejects the
string rather than misreading it.
"""
from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

_T = TypeVar("_T", bound=BaseModel)

#: A fenced block, with or without a language tag. Non-greedy so the first
#: closing fence wins; a reply with two fences still yields the first block.
_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def strip_code_fence(text: str) -> str:
    """Return the inside of the first fenced block, or ``text`` unchanged."""
    match = _FENCE.search(text)
    return match.group(1).strip() if match else text.strip()


def _balanced_span(text: str, open_ch: str, close_ch: str) -> str | None:
    """Return the first balanced ``open_ch``..``close_ch`` span, string-aware.

    Braces inside a quoted value (``{"note": "use {} carefully"}``) and escaped
    quotes must not change the depth count, so the scan tracks whether it is
    inside a JSON string. Returns ``None`` when no span closes.
    """
    start = text.find(open_ch)
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == open_ch:
            depth += 1
        elif char == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def extract_json_object(raw: str | None) -> dict[str, Any] | None:
    """Return the JSON object in a model reply, or ``None``.

    Tolerates a bare object, a fenced block, and prose before or after the
    object. Never raises and never guesses: an unparseable reply is ``None``.
    """
    if not raw:
        return None
    text = strip_code_fence(str(raw))
    if not text:
        return None

    # Fast path: the reply is already one clean value.
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        return parsed

    # Slow path: pull the first balanced object out of surrounding text.
    span = _balanced_span(text, "{", "}")
    if span is None:
        return None
    try:
        parsed = json.loads(span)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def extract_json_array(raw: str | None) -> list[Any] | None:
    """Return the JSON array in a model reply, or ``None``.

    The array counterpart to :func:`extract_json_object`, with the same
    fence/prose tolerance.
    """
    if not raw:
        return None
    text = strip_code_fence(str(raw))
    if not text:
        return None

    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, list):
        return parsed

    span = _balanced_span(text, "[", "]")
    if span is None:
        return None
    try:
        parsed = json.loads(span)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, list) else None


def validate_object(raw: str | None, model_cls: type[_T]) -> _T | None:
    """Parse ``raw`` as a JSON object and validate it against ``model_cls``.

    Returns the model instance or ``None`` when the reply is not an object, is
    unparseable, or fails validation. Fail-open in the caller's direction: a
    ``None`` here means "the model did not give us a usable structured answer",
    which is the recoverable outcome.
    """
    data = extract_json_object(raw)
    if data is None:
        return None
    try:
        return model_cls.model_validate(data)
    except ValidationError:
        return None
