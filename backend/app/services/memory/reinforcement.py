"""Shared best-effort reinforcement for memories injected into a prompt.

The orchestrator, direct /messages endpoints and RAG search all recall long-term
memories. Keeping the write-back here means each path reinforces a successfully
used fact the same way, without any of them depending on the orchestrator's
LangGraph module.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)


async def reinforce_recalled_memories(user_id: str, recalled_items: list[dict[str, Any]]) -> None:
    """Raise confidence for memories that were actually injected into a prompt.

    Fail-open by design: this runs after the answer is complete, so a bad user
    id, a missing local row or a database outage must never fail the turn.
    """
    from backend.app.domain.user.repository import UserRepository
    from backend.app.infrastructure.database.session import async_session_factory

    try:
        _ = UUID(str(user_id))
    except (ValueError, TypeError, AttributeError):
        return

    db_ids: list[UUID] = []
    mem0_ids: list[str] = []
    for item in recalled_items:
        if not isinstance(item, dict):
            continue
        if item.get("db_id"):
            try:
                db_ids.append(UUID(str(item["db_id"])))
            except (ValueError, TypeError):
                pass
        elif item.get("id"):
            try:
                db_ids.append(UUID(str(item["id"])))
            except (ValueError, TypeError):
                mem0_ids.append(str(item["id"]))

    try:
        async with async_session_factory() as session:
            repo = UserRepository(session)
            if mem0_ids:
                resolved_ids = await repo.find_memory_ids_by_mem0_ids(mem0_ids)
                db_ids.extend(resolved_ids)
            if db_ids:
                count = await repo.reinforce_memories(db_ids)
                logger.info("recalled_memories_reinforced", user_id=user_id, count=count)
    except Exception as exc:
        logger.warning("reinforce_memories_failed_non_blocking", error=str(exc))
