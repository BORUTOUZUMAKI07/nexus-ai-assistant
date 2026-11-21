"""Handlers for the two post-answer task types.

These are the exact operations that used to run inline after the persistence
commit. They live here rather than in the webhook module so ``service.py`` and
the executor stay independent of the HTTP client, and so the worker can hold
one registry instead of an if/elif chain.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from backend.app.services.memory.semantic_cache import semantic_response_cache
from backend.app.services.outbox.service import TASK_CACHE_INVALIDATION, TASK_WEBHOOK_DISPATCH
from backend.app.services.webhooks.service import WebhookService
from sqlmodel.ext.asyncio.session import AsyncSession

TaskHandler = Callable[[AsyncSession, dict[str, Any]], Awaitable[None]]


async def invalidate_semantic_cache(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Drop the cache bucket the new answer may have made stale.

    Older queued payloads did not record the conversation or prompt variant;
    their defaults keep them working while new jobs invalidate exactly the
    scope that was written.
    """
    await semantic_response_cache.invalidate(
        user_id=payload["user_id"],
        model=payload["model"],
        prompt_variant=payload.get("prompt_variant", "default"),
        conversation_id=payload.get("conversation_id", ""),
    )


async def dispatch_webhook(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Deliver `message.completed` with the same body the inline path used."""
    await WebhookService(session).dispatch_event(
        event="message.completed",
        payload={
            "conversation_id": str(payload["conversation_id"]),
            "message_id": str(payload["message_id"]),
            "role": "assistant",
            "model": payload["model"],
            "content": payload["content"],
        },
        user_id=UUID(payload["user_id"]),
    )


HANDLERS: dict[str, TaskHandler] = {
    TASK_CACHE_INVALIDATION: invalidate_semantic_cache,
    TASK_WEBHOOK_DISPATCH: dispatch_webhook,
}
