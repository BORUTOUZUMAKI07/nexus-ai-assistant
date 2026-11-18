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

from backend.app.core.exceptions import ValidationError

# A client replays the visible transcript; it must never be able to inject a
# privileged turn. The agent's system prompt is compiled server-side from the
# conversation's own `system_prompt` plus active skills, so a client-supplied
# `system`/`developer` role would overwrite that trust boundary.
_ALLOWED_REPLAY_ROLES = frozenset({"user", "assistant", "tool"})


def _sanitize_replayed_message(msg: dict[str, Any]) -> dict[str, Any]:
    from backend.app.services.evaluation.guardrail_service import guardrail_service

    if not isinstance(msg, dict):
        return msg

    role = msg.get("role")
    if role is not None and role not in _ALLOWED_REPLAY_ROLES:
        # Raises ValidationError -> the caller maps this to a 422. A benign
        # system-role turn used to survive because guardrail validation only
        # inspected `content`, so role injection was possible whenever the
        # injected text itself looked harmless.
        raise ValidationError(f"Disallowed message role in replayed history: {role!r}")

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
