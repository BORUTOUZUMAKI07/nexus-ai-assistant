"""Cached, read-only status of the services this app depends on.

``/health`` is liveness and ``/health/ready`` is the database gate. Neither says
anything about Qdrant, mem0, the OTLP collector, storage or email. This module
adds a third probe that is safe to call often: every check is either a bounded
read-only request or a credential/state check, and the full result is cached for
``HEALTH_DEPENDENCIES_CACHE_TTL_SECONDS``.

Only statuses and error *types* leave this module. API keys, DSNs and auth
headers never appear in the response.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from backend.app.core.config import settings
from backend.app.core.logging import get_logger
from backend.app.infrastructure.cache.redis_client import redis_client
from backend.app.infrastructure.database.engine import check_database_health
from backend.app.infrastructure.vector.qdrant_client import vector_db
from backend.app.services.memory.long_term import long_term_memory
from backend.app.services.observability.metrics import metrics_collector
from backend.app.services.observability.otel import collector_endpoint_resolvable
from backend.app.services.observability.startup_verification import get_startup_telemetry_status

logger = get_logger("nexus.health")

OK = "ok"
DEGRADED = "degraded"
NOT_CONFIGURED = "not_configured"

# Mem0 cloud has a single read-only ping endpoint. Kept as a constant so tests
# can replace it without touching a real account.
_MEM0_PING_URL = "https://api.mem0.ai/v1/ping/"

_now = time.monotonic
_CACHE: dict[str, Any] | None = None
_EXPIRES_AT = 0.0
_LOCK = asyncio.Lock()


def clear_dependency_health_cache() -> None:
    """Drop the cached snapshot (used by tests and by explicit refresh calls)."""
    global _CACHE, _EXPIRES_AT
    _CACHE = None
    _EXPIRES_AT = 0.0


def _latency_ms(started: float) -> float:
    return round((_now() - started) * 1000, 1)


async def _timed(
    name: str,
    call: Callable[[], Awaitable[Any]],
    timeout: float,
    *,
    is_ok: Callable[[Any], bool] = bool,
    detail_on_fail: str = "unhealthy",
) -> dict[str, Any]:
    """Run one probe with a hard timeout; a failure is data, never an exception."""
    started = _now()
    try:
        value = await asyncio.wait_for(call(), timeout=timeout)
    except TimeoutError:
        logger.warning("dependency_probe_timeout", dependency=name, timeout_seconds=timeout)
        return {"status": DEGRADED, "detail": "timeout", "latency_ms": _latency_ms(started)}
    except Exception as exc:  # noqa: BLE001 - a probe must never break health
        logger.warning("dependency_probe_failed", dependency=name, error_type=type(exc).__name__)
        return {"status": DEGRADED, "detail": type(exc).__name__, "latency_ms": _latency_ms(started)}
    if not is_ok(value):
        return {"status": DEGRADED, "detail": detail_on_fail, "latency_ms": _latency_ms(started)}
    return {"status": OK, "latency_ms": _latency_ms(started)}


async def _probe_database(timeout: float) -> dict[str, Any]:
    return await _timed("database", check_database_health, timeout)


async def _probe_redis(timeout: float) -> dict[str, Any]:
    return await _timed("redis", redis_client.ping, timeout)


async def _probe_qdrant(timeout: float) -> dict[str, Any]:
    # get_collections() is read-only. Never call ensure_collection() here: it can
    # create indexes and, on a dimension mismatch, delete the collection.
    return await _timed("qdrant", vector_db.client.get_collections, timeout)


async def _probe_storage(timeout: float) -> dict[str, Any]:
    async def fetch_bucket() -> bool:
        base = str(settings.SUPABASE_URL).rstrip("/")
        url = f"{base}/storage/v1/bucket/{settings.STORAGE_BUCKET}"
        headers = {
            "apikey": str(settings.SUPABASE_SERVICE_ROLE_KEY),
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers)
            return response.status_code == 200

    return await _timed("storage", fetch_bucket, timeout, detail_on_fail="bucket_unavailable")


def _configured(value: Any) -> dict[str, Any]:
    return {"status": OK if value else NOT_CONFIGURED}


def _mem0_status() -> dict[str, Any]:
    """Report the mode the app will actually use, before any network call."""
    client = getattr(long_term_memory, "_client", None)
    if client is None:
        if not settings.MEM0_API_KEY:
            return {"status": NOT_CONFIGURED, "mode": "noop"}
        return {"status": DEGRADED, "mode": "noop", "detail": "cloud_client_unavailable"}
    name = type(client).__name__
    if "MemoryClient" in name:
        return {"status": OK, "mode": "cloud"}
    return {"status": OK, "mode": "self_hosted"}


async def _probe_mem0(timeout: float) -> dict[str, Any]:
    """Verify a configured Mem0 cloud client with a bounded read-only ping.

    The SDK pings only at client construction, so an object can exist long
    after its key was revoked. Self-hosted mode is left at its state check:
    its backing Qdrant and LLM services are probed elsewhere. No key is ever
    returned — only status, mode, detail and latency.
    """
    base = _mem0_status()
    if base["status"] != OK or base.get("mode") != "cloud":
        return base

    async def ping() -> bool:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                _MEM0_PING_URL,
                headers={"Authorization": f"Token {settings.MEM0_API_KEY}"},
            )
            return response.status_code == 200

    probed = await _timed("mem0", ping, timeout, detail_on_fail="ping_failed")
    return {**base, **probed}


def _e2b_status() -> dict[str, Any]:
    key = settings.E2B_API_KEY
    if not key or str(key).startswith("e2b_placeholder"):
        return {"status": NOT_CONFIGURED}
    return {"status": OK}


def _new_relic_status() -> dict[str, Any]:
    if not settings.NEW_RELIC_ENABLED:
        return {"status": NOT_CONFIGURED}
    if not (settings.OTLP_AUTH_USER and settings.OTLP_AUTH_PASSWORD):
        return {"status": DEGRADED, "detail": "credentials_missing"}
    if not collector_endpoint_resolvable():
        return {"status": DEGRADED, "detail": "collector_unresolvable"}
    return {"status": OK}


def _langsmith_status() -> dict[str, Any]:
    if not settings.LANGSMITH_TRACING:
        return {"status": NOT_CONFIGURED}
    if not settings.LANGSMITH_API_KEY:
        return {"status": DEGRADED, "detail": "api_key_missing"}
    return {"status": OK, "detail": "sdk_tracing_enabled"}


def _email_status() -> dict[str, Any]:
    if settings.RESEND_API_KEY:
        return {"status": OK, "channel": "resend"}
    if settings.SMTP_USER and settings.SMTP_PASSWORD:
        return {"status": OK, "channel": "smtp"}
    return {"status": NOT_CONFIGURED, "channel": "dev_sink"}


def _fallback_counters() -> dict[str, int]:
    counters = metrics_collector.get_summary().get("counters", {})
    visible = {"redis_fail_open"}  # canonical counter already emitted by the rate limiter
    return {
        key: value
        for key, value in sorted(counters.items())
        if key.startswith("dependency_fallback:") or key in visible
    }


async def _collect() -> dict[str, Any]:
    timeout = settings.HEALTH_DEPENDENCIES_TIMEOUT_SECONDS
    storage_configured = bool(
        settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY and settings.STORAGE_BUCKET
    )
    services: dict[str, Any] = {
        "database": await _probe_database(timeout),
        "redis": await _probe_redis(timeout),
        "qdrant": await _probe_qdrant(timeout),
        "mem0": await _probe_mem0(timeout),
        "e2b": _e2b_status(),
        "firecrawl": _configured(settings.FIRECRAWL_API_KEY),
        "tavily": _configured(settings.TAVILY_API_KEY),
        "new_relic": _new_relic_status(),
        "langsmith": _langsmith_status(),
        "storage": await _probe_storage(timeout) if storage_configured else {"status": NOT_CONFIGURED},
        "email": _email_status(),
    }
    overall = DEGRADED if any(item["status"] == DEGRADED for item in services.values()) else OK
    return {
        "status": overall,
        "checked_at_ms": int(time.time() * 1000),
        "cache_ttl_seconds": settings.HEALTH_DEPENDENCIES_CACHE_TTL_SECONDS,
        "services": services,
        "startup_telemetry": get_startup_telemetry_status(),
        "fallback_counters": _fallback_counters(),
    }


async def get_dependency_health() -> dict[str, Any]:
    """Return one snapshot per TTL window, never parallel network storms."""
    global _CACHE, _EXPIRES_AT
    now = _now()
    if _CACHE is not None and now < _EXPIRES_AT:
        return _CACHE
    async with _LOCK:
        now = _now()
        if _CACHE is not None and now < _EXPIRES_AT:
            return _CACHE
        result = await _collect()
        _CACHE = result
        _EXPIRES_AT = _now() + settings.HEALTH_DEPENDENCIES_CACHE_TTL_SECONDS
        return result
