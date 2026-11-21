"""Single gate for the user's automatic-memory preference.

``enable_memory`` is an explicit opt-out, not a feature flag. A missing row, an
invalid id or a settings lookup failure all keep today's default-enabled,
fail-open behaviour; only a stored ``False`` disables automatic memory reads and
writes. Manual memory management and GDPR operations intentionally do not call
this gate.
"""
from __future__ import annotations

from uuid import UUID

import structlog
from backend.app.domain.user.repository import UserRepository
from backend.app.infrastructure.database.session import async_session_factory
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


async def is_memory_enabled(user_id: str | UUID, session: AsyncSession | None = None) -> bool:
    """Return False only when this user explicitly disabled automatic memory."""
    try:
        uid = UUID(str(user_id))
    except (ValueError, TypeError, AttributeError):
        return True

    try:
        if session is not None:
            return await UserRepository(session).is_memory_enabled(uid)
        async with async_session_factory() as owned_session:
            return await UserRepository(owned_session).is_memory_enabled(uid)
    except Exception as exc:
        logger.warning(
            "memory_preference_lookup_failed_fail_open",
            user_id=str(uid),
            error=str(exc),
        )
        return True
