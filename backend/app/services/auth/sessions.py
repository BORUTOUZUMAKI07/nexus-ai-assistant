"""Best-effort sign-in session inventory backed by Redis.

A session is identified by the refresh-token family id (``fam``), not the access
token ``jti``: jti rotates on every refresh while fam survives for the entire
sign-in. The registry is an *index for the user*, never an authorization source
— revocation still relies on the ``refresh:revoked:*`` markers in AuthService.
A Redis outage therefore only hides the session list; it cannot keep a revoked
session alive or prevent a sign-in.
"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from backend.app.infrastructure.cache.redis_client import get_cache_service

logger = structlog.get_logger(__name__)

SESSION_PREFIX = "session:"
# A person does not have thousands of live sign-ins. Bounding the inventory
# keeps a corrupt or hostile key space from turning a settings page into a
# multi-minute SCAN over the whole Redis instance.
MAX_SESSIONS = 100
MAX_USER_AGENT_LEN = 200


def session_key(user_id: UUID, family_id: str) -> str:
    """Key for one sign-in. The user id is part of the key so a guessed family
    id can never address somebody else's session."""
    return f"{SESSION_PREFIX}{user_id}:{family_id}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _ttl(expires_at: int) -> int:
    return max(1, expires_at - int(datetime.now(UTC).timestamp()))


async def record_session(user_id: UUID, family_id: str, expires_at: int, user_agent: str = "") -> None:
    """Create a fresh inventory row for a new sign-in."""
    now = _now()
    payload = {
        "created_at": now,
        "last_seen": now,
        "expires_at": expires_at,
        "user_agent": user_agent[:MAX_USER_AGENT_LEN],
    }
    try:
        await get_cache_service().set(
            session_key(user_id, family_id),
            json.dumps(payload),
            ttl_seconds=_ttl(expires_at),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("session_record_failed", error=type(exc).__name__)


async def touch_session(user_id: UUID, family_id: str, expires_at: int) -> None:
    """Advance the refresh lifetime on an existing session after rotation."""
    key = session_key(user_id, family_id)
    try:
        raw = await get_cache_service().get(key)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("session_touch_read_failed", error=type(exc).__name__)
        return
    if not raw:
        # A session minted before the inventory existed: start one now rather
        # than leaving a valid sign-in invisible in the settings page.
        await record_session(user_id, family_id, expires_at)
        return
    try:
        payload = json.loads(raw)
        payload["last_seen"] = _now()
        payload["expires_at"] = expires_at
        await get_cache_service().set(key, json.dumps(payload), ttl_seconds=_ttl(expires_at))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("session_touch_write_failed", error=type(exc).__name__)


async def _read_session_row(key: str) -> dict[str, Any] | None:
    """Read one inventory row, or ``None`` if it is gone or unreadable.

    Keeping the failure handling here (with ``return`` rather than ``continue``)
    lets the caller stay a plain loop. A bad row only hides itself; it never
    fails the whole inventory.
    """
    try:
        raw = await get_cache_service().get(key)
        if not raw:
            return None
        payload = json.loads(raw)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("session_row_read_failed", error=type(exc).__name__)
        return None
    if not isinstance(payload, dict):
        return None
    return {"family_id": key.rsplit(":", 1)[-1], **payload}


async def list_sessions(user_id: UUID) -> list[dict[str, Any]]:
    """Return the caller's sessions, newest first. Malformed rows are skipped."""
    try:
        keys = await get_cache_service().scan(f"{SESSION_PREFIX}{user_id}:", limit=MAX_SESSIONS)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("session_list_failed", error=type(exc).__name__)
        return []
    rows: list[dict[str, Any]] = []
    for key in keys:
        row = await _read_session_row(key)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
    return rows


async def delete_session(user_id: UUID, family_id: str) -> None:
    """Remove one inventory row. Revocation itself happens in AuthService."""
    try:
        await get_cache_service().delete(session_key(user_id, family_id))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("session_delete_failed", error=type(exc).__name__)


async def delete_user_sessions(user_id: UUID) -> None:
    """Drop every inventory row for a user (password reset / user-wide revoke)."""
    try:
        keys = await get_cache_service().scan(f"{SESSION_PREFIX}{user_id}:", limit=MAX_SESSIONS)
        for key in keys:
            await get_cache_service().delete(key)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("session_delete_user_failed", error=type(exc).__name__)
