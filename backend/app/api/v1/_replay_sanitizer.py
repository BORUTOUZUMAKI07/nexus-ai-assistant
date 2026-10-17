"""Guardrail pass over every replayed message before it reaches the graph.

The caller (`conversations.py`) sanitizes *all* replayed turns, not just the
last one, and turns any failure into a 422. That only works if this module lets
the guardrail's rejection out: it used to catch broadly and blank the content,
so an injected `role: "system"` turn was silently replaced with `""` and the
request proceeded — the 422 was unreachable and the comment claiming otherwise
was false (audit finding #7). `guardrail_service.validate_input` raising
`ValidationError` on injection is the intended signal; it must propagate.
"""
from typing import Any


def _sanitize_replayed_message(msg: dict[str, Any]) -> dict[str, Any]:
    from backend.app.services.evaluation.guardrail_service import guardrail_service

    if not isinstance(msg, dict):
        return msg

    sanitized = dict(msg)
    content = sanitized.get("content")

    if content is None:
        return sanitized

    if isinstance(content, str) and content:
        # Raises ValidationError on injection; the caller maps that to a 422.
        sanitized["content"] = guardrail_service.validate_input(content)
        return sanitized

    if isinstance(content, list):
        new_parts: list[dict[str, Any]] = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str):
                new_parts.append({**p, "text": guardrail_service.validate_input(p["text"])})
            else:
                new_parts.append(p)
        sanitized["content"] = new_parts

    return sanitized
