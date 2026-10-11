import asyncio
from typing import Any, Literal
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)


def _sanitize_replayed_message(msg: dict[str, Any]) -> dict[str, Any]:
    from backend.app.services.evaluation.guardrail_service import guardrail_service

    if not isinstance(msg, dict):
        return msg

    sanitized = dict(msg)
    content = sanitized.get("content")

    if content is None:
        return sanitized

    if isinstance(content, str) and content:
        try:
            sanitized["content"] = guardrail_service.validate_input(content)
        except Exception:
            sanitized["content"] = ""
        return sanitized

    if isinstance(content, list):
        new_parts: list[dict[str, Any]] = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str):
                try:
                    red = guardrail_service.validate_input(p["text"])
                except Exception:
                    red = ""
                new_parts.append({**p, "text": red})
            else:
                new_parts.append(p)
        sanitized["content"] = new_parts

    return sanitized
