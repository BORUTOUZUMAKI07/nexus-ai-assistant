"""
Rate-limit dependency — industry-standard 429 semantics (RFC 6585) plus
standard rate-limit response headers.

This is the API-surface half of rate limiting (the tool gateway already applies
a per-user ceiling internally). It:

* enforces a sliding-window token bucket per (scope, key) using Redis' atomic
  Lua script (``RedisService.check_rate_limit``),
* writes ``X-RateLimit-Limit`` / ``X-RateLimit-Remaining`` / ``X-RateLimit-Reset``
  on every response so clients can self-throttle,
* answers 429 with a ``Retry-After`` header (seconds) when the limit is hit,
* FAILS OPEN when Redis is unreachable — an offline cache must never nuke
  traffic (app-wide convention).

Two flavors:
  ``rate_limit(scope, ...)``        — authenticated endpoints, keyed per user
                                      (optionally per-org via ``key_builder``).
  ``rate_limit_anon(scope, ...)``   — unauthenticated endpoints (login/register),
                                      keyed by client IP.

Usage::

    @router.get("/search")
    async def search(..., _rl: None = Depends(rate_limit("search", limit=60))):
        ...
"""
from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

import structlog
from backend.app.api.deps import get_current_user, get_db
from backend.app.core.config import settings
from backend.app.domain.user.models import User
from backend.app.infrastructure.cache.redis_client import redis_service
from backend.app.services.observability.metrics import metrics_collector
from fastapi import Depends, Request, Response, status
from fastapi.responses import JSONResponse
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)

RATE_LIMIT_HEADER_LIMIT = "X-RateLimit-Limit"
RATE_LIMIT_HEADER_REMAINING = "X-RateLimit-Remaining"
RATE_LIMIT_HEADER_RESET = "X-RateLimit-Reset"
RETRY_AFTER_HEADER = "Retry-After"


class RateLimitError(Exception):
    """Internal sentinel converted to a 429 JSON response with Retry-After.

    Carries the full standard header payload (``Retry-After`` plus
    ``X-RateLimit-*``) so the exception handler emits them on the 429. The
    injected response's headers are discarded the moment an exception handler
    returns a fresh response, so the payload must travel with the exception.
    """

    def __init__(
        self,
        retry_after_seconds: int,
        limit: int = 0,
        remaining: int = 0,
        window_seconds: int | None = None,
    ):
        super().__init__(f"Rate limit exceeded. Retry after {retry_after_seconds}s.")
        self.retry_after_seconds = retry_after_seconds
        window = window_seconds or retry_after_seconds
        self.headers = {
            RETRY_AFTER_HEADER: str(retry_after_seconds),
            RATE_LIMIT_HEADER_LIMIT: str(limit),
            RATE_LIMIT_HEADER_REMAINING: str(max(0, remaining)),
            RATE_LIMIT_HEADER_RESET: str(window),
        }


def _build_header_payload(limit: int, remaining: int, window_seconds: int) -> dict[str, str]:
    return {
        RATE_LIMIT_HEADER_LIMIT: str(limit),
        RATE_LIMIT_HEADER_REMAINING: str(max(0, remaining)),
        # Reset in seconds: the sliding window resets continuously, so we report
        # the window itself — standard practice without fixed-window boundaries.
        RATE_LIMIT_HEADER_RESET: str(window_seconds),
    }


async def _check_and_enforce(
    request: Request,
    response: Response,
    identifier: str,
    limit: int,
    window_seconds: int,
) -> None:
    try:
        allowed, remaining = await redis_service.check_rate_limit(
            identifier, limit=limit, window_seconds=window_seconds
        )
    except Exception as exc:
        # Fail-open: offline Redis must not block traffic. Count the event so
        # Prometheus can page on a Redis outage via nexus_requests_total{operation="redis_fail_open"}.
        metrics_collector.increment("redis_fail_open")
        logger.warning("rate_limit_check_failed_fail_open", identifier=identifier, error=str(exc))
        return

    payload = _build_header_payload(limit, remaining, window_seconds)
    # Stash on request.state as well: an exception handler returns a fresh
    # response that discards the injected Response's headers, so the main app's
    # HTTPException handler re-emits these from request.state.
    request.state.rate_limit_headers = payload
    for name, value in payload.items():
        response.headers[name] = value

    if not allowed:
        metrics_collector.increment("rate_limit_denied")
        raise RateLimitError(
            retry_after_seconds=window_seconds,
            limit=limit,
            remaining=remaining,
            window_seconds=window_seconds,
        )


def org_scoped_key(scope: str, user_id: UUID, org_id: UUID | None) -> str:
    """Per-org rate-limit bucket, with a per-user fallback for non-org users.

    Multi-tenant isolation: all members of an org share a single bucket, so a
    busy tenant burns only its own budget and cannot starve other tenants.
    Users without an org are their own tenant and keep a per-user bucket.
    """
    if org_id is not None:
        return f"{scope}:org:{org_id}"
    return f"{scope}:user:{user_id}"


async def _resolve_org_id_for_user(session: AsyncSession, user_id: UUID) -> UUID | None:
    from backend.app.services.org_service import OrganizationService

    return await OrganizationService(session).resolve_org_id(user_id)


# Module-level hook so tests can substitute a fake org resolver (mirrors the
# ``redis_service`` seam below).
resolve_org_id_for_user = _resolve_org_id_for_user


def rate_limit(
    scope: str,
    limit: int | None = None,
    window_seconds: int = 60,
    key_builder: Callable[[Request, User], str] | None = None,
    org_scope: bool = False,
) -> Any:
    """FastAPI dependency for authenticated endpoints, keyed per user by default.

    ``key_builder`` can fold in extra key material, e.g.
    ``lambda req, u: f"{scope}:{u.id}:{org_id}"`` for custom per-org keys.

    ``org_scope`` switches to the standard per-org bucket (see
    :func:`org_scoped_key`): members of an organization share one bucket and
    users without an org keep a per-user bucket. This is the multi-tenant
    isolation default for high-volume endpoints (tools/files).
    """
    bucket_limit = limit or settings.RATE_LIMIT_PER_MINUTE

    def _default_key(request: Request, current_user: User) -> str:
        return f"{scope}:{current_user.id}"

    async def dependency(
        request: Request,
        response: Response,
        current_user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db),
    ) -> None:
        if org_scope:
            org_id = await resolve_org_id_for_user(session, current_user.id)
            key = org_scoped_key(scope, current_user.id, org_id)
        else:
            builder = key_builder or _default_key
            key = builder(request, current_user)
        await _check_and_enforce(request, response, key, bucket_limit, window_seconds)

    return Depends(dependency)


def rate_limit_anon(
    scope: str,
    limit: int,
    window_seconds: int = 60,
) -> Any:
    """FastAPI dependency for unauthenticated endpoints, keyed by client IP.

    Used on login/register so brute-forcing is throttled before any user lookup.
    The client IP comes from Starlette's request.client; behind a proxy the
    ``X-Forwarded-For`` header is honored by the deployment's proxy config.
    """
    async def dependency(request: Request, response: Response) -> None:
        client_ip = request.client.host if request.client else "unknown"
        await _check_and_enforce(request, response, f"{scope}:ip:{client_ip}", limit, window_seconds)

    return Depends(dependency)


def rate_limit_error_handler(request: Request, exc: RateLimitError) -> JSONResponse:
    """Starlette exception handler converting RateLimitError into RFC 6585 429."""
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "type": "https://nexus-assistant.ai/errors/rate-limit-exceeded",
            "title": "RATE_LIMIT_EXCEEDED",
            "status": status.HTTP_429_TOO_MANY_REQUESTS,
            "detail": str(exc),
        },
        headers=exc.headers,
    )
