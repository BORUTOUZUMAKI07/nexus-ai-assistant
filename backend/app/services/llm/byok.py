"""Bring-Your-Own-Key (BYOK) resolution.

A user may store an encrypted provider key (the ``api_keys`` table, written by
``/settings/keys``). Storing it is not the feature -- *using* it is: the raw key
has to reach the provider call the user's request actually makes. This module is
that bridge, and without it the whole BYOK surface is write-only (add/list/delete
keys that change nothing about how a request is served).

The decrypted key lives in a :class:`ContextVar` for the duration of one run
only. It is never persisted, never logged, and never returned to a client. The
binding site (``run_executor.execute_run`` for the streamed chat path, and the
HITL resume endpoint) resets it in a ``finally`` so one user's key cannot leak
into a later run served by the same task -- the same reasoning and the same
failure mode as the spend meter in ``core/spend.py``.

Fail-open is total: a database error, a decryption error, or an unknown provider
all degrade to "no user key", i.e. the shared deployment key is used exactly as
before. A broken BYOK lookup must never take a run down, and a *raised* meter
bug is the only thing this layer lets surface (there is nothing to hide here --
the raw key either decrypts or it does not).
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any
from uuid import UUID

import structlog
from backend.app.core.security import decrypt_api_key

logger = structlog.get_logger(__name__)

# provider (lowercase) -> raw key. Empty means "no BYOK bound in this context".
_byok_keys: ContextVar[dict[str, str]] = ContextVar("nexus_byok_keys", default={})


def bind_byok_keys(keys: dict[str, str] | None) -> Token[dict[str, str]]:
    """Bind the user's keys to the current context and return the reset token.

    Provider names are lowercased on the way in so ``get_byok_key`` (which
    lowercases its argument) matches a row stored as ``"Groq"``. Normalising at
    the one write point keeps the read a plain dict lookup rather than a scan.
    """
    return _byok_keys.set(
        {
            str(k).strip().lower(): v
            for k, v in (keys or {}).items()
            if k and v
        }
    )


def reset_byok_keys(token: Token[dict[str, str]]) -> None:
    """Restore the previous keys. Must be called in a ``finally``.

    The catch is deliberately wide, mirroring ``core.spend.reset_spend_meter``:
    ``ContextVar.reset`` raises ``ValueError`` for a token from another context
    and ``TypeError`` for a wrong-typed one, and an unrecognised token is not a
    reason to leave *one user's decrypted key* bound for the next request served
    by this task. Dropping to "no keys" degrades to the shared deployment key,
    which is the safe direction; keeping it would be a credential leak.
    """
    try:
        _byok_keys.reset(token)
    except Exception:
        # A consumed token raises RuntimeError, a cross-context one ValueError, a
        # wrong-typed one TypeError. The safe state is "no keys", so drop them
        # rather than re-raising into whatever finally is unwinding.
        _byok_keys.set({})


def clear_byok_keys() -> None:
    _byok_keys.set({})


def byok_keys() -> dict[str, str]:
    """A copy of the keys bound in this context. For tests and diagnostics."""
    return dict(_byok_keys.get())


def get_byok_key(provider: str | None) -> str | None:
    """The user's key for ``provider``, or ``None``.

    ``provider`` is the bare provider name (``groq`` / ``openrouter`` /
    ``gemini`` ...), which is what ``resolve_provider`` returns and what the
    ``api_keys.provider`` column stores. The lookup is case-insensitive so a row
    stored as ``"Groq"`` still matches.
    """
    if not provider:
        return None
    return _byok_keys.get().get(provider.strip().lower()) or None


async def load_user_byok_keys(session: Any, user_id: UUID | None) -> dict[str, str]:
    """Decrypt every active provider key the user has stored.

    Returns ``{}`` on any failure -- a BYOK lookup that cannot run is not a
    reason for the user's request to fail. A row whose ciphertext will not
    decrypt (for example after a rotated ``ENCRYPTION_KEY``) is skipped rather
    than aborting the lookup for the user's other providers.
    """
    if user_id is None:
        return {}

    from backend.app.domain.user.models import APIKey
    from sqlmodel import select

    keys: dict[str, str] = {}
    try:
        result = await session.exec(
            select(APIKey).where(
                APIKey.user_id == user_id,
                APIKey.is_active == True,  # noqa: E712 - repo idiom, matched to UserRepository
            )
        )
        rows = list(result.all())
    except Exception as exc:
        logger.warning(
            "byok_load_failed", user_id=str(user_id), error_type=type(exc).__name__
        )
        return {}

    for row in rows:
        provider = (getattr(row, "provider", "") or "").strip().lower()
        if not provider:
            continue
        try:
            raw = decrypt_api_key(row.encrypted_key)
        except Exception as exc:
            logger.warning(
                "byok_decrypt_failed",
                provider=provider,
                error_type=type(exc).__name__,
            )
            continue
        if raw:
            keys[provider] = raw
    return keys
