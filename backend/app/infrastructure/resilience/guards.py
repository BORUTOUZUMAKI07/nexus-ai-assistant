"""
Resilience guards — HLD patterns: circuit breaker, bulkhead in-flight cap,
singleton-task lock (leader-election lite), and Idempotency-Key replay guard.

Every guard takes an injectable backend (cache store / none) so each piece is
unit-testable against the in-memory FakeSession/fakes without Docker or Redis.

Fail-open policy, per the app-wide convention: an unavailable support service
(e.g. Redis) must never block traffic. The single deliberate exception is the
singleton task lock, where a store outage *skips* this cycle — double-running a
periodic job is worse than skipping one beat, and the next beat retries anyway.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from backend.app.infrastructure.cache.base import ICacheService
from backend.app.infrastructure.cache.redis_client import redis_service

logger = structlog.get_logger(__name__)


class CircuitBreaker:
    """Per-name state machine guarding external providers.

    CLOSED → OPEN (after ``failure_threshold`` consecutive failures) →
    HALF-OPEN (one probe allowed after ``open_timeout_seconds``) → CLOSED on
    probe success, back to OPEN on probe failure. Exactly one probe passes the
    half-open window; concurrent callers still see OPEN and skip the group.
    """

    def __init__(self, failure_threshold: int = 5, open_timeout_seconds: float = 60.0) -> None:
        self._failure_threshold = max(1, failure_threshold)
        self._open_timeout = max(0.001, float(open_timeout_seconds))
        self._states: dict[str, str] = {}
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def _current_state(self, name: str) -> str:
        state = self._states.get(name, "closed")
        if state == "open" and (time.monotonic() - self._opened_at.get(name, 0.0)) >= self._open_timeout:
            self._states[name] = "half_open"
            return "half_open"
        return state

    async def allow(self, name: str) -> bool:
        """True when a call to ``name`` may proceed."""
        async with self._lock:
            state = self._current_state(name)
            if state == "half_open":
                # Consume the single probe slot: re-OPEN synchronously so any
                # concurrent caller still skips; record_success() closes it.
                self._states[name] = "open"
                self._opened_at[name] = time.monotonic()
                return True
            return state != "open"

    async def record_success(self, name: str) -> None:
        async with self._lock:
            self._failures.pop(name, None)
            self._opened_at.pop(name, None)
            self._states[name] = "closed"
            logger.debug("circuit_breaker_closed", name=name)

    async def record_failure(self, name: str) -> None:
        async with self._lock:
            failures = self._failures.get(name, 0) + 1
            self._failures[name] = failures
            if failures >= self._failure_threshold:
                self._states[name] = "open"
                self._opened_at[name] = time.monotonic()
                logger.warning("circuit_breaker_opened", name=name, failures=failures)

    def reset(self) -> None:
        """Clear all per-name state (test hooks / operator reset)."""
        self._states.clear()
        self._failures.clear()
        self._opened_at.clear()

    def stats(self, name: str) -> dict[str, Any]:
        return {"state": self._current_state(name), "failures": self._failures.get(name, 0)}


class InFlightLimiter:
    """Bulkhead: caps concurrent in-flight calls per name (provider group).

    An ``asyncio.Semaphore`` per name; a long-running stream occupies its slot
    for the whole stream so a few heavy calls cannot starve other groups.
    """

    def __init__(self, max_in_flight: int = 8) -> None:
        self._max_in_flight = max(1, int(max_in_flight))
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def _semaphore(self, name: str) -> asyncio.Semaphore:
        sem = self._semaphores.get(name)
        if sem is None:
            sem = asyncio.Semaphore(self._max_in_flight)
            self._semaphores[name] = sem
        return sem

    @asynccontextmanager
    async def acquire(self, name: str) -> AsyncIterator[None]:
        sem = self._semaphore(name)
        await sem.acquire()
        try:
            yield
        finally:
            sem.release()


class TaskLockStore:
    """Redis-backed distributed lock for singleton periodic jobs."""

    def __init__(self, cache: ICacheService | None = None) -> None:
        self._cache = cache if cache is not None else redis_service

    @staticmethod
    def _key(name: str) -> str:
        return f"singleton_lock:{name}"

    async def try_acquire(self, name: str, ttl_seconds: int) -> bool:
        return await self._cache.set_if_absent(self._key(name), "1", ttl_seconds)

    async def release(self, name: str) -> None:
        await self._cache.delete(self._key(name))


_lock_store = TaskLockStore()


@asynccontextmanager
async def singleton_lock(
    name: str,
    ttl_seconds: int = 300,
    store: TaskLockStore | None = None,
) -> AsyncIterator[bool]:
    """Yield True when this process holds the lock for ``name``.

    On a store outage the cycle is *skipped* (yields False) — never double-run.
    """
    store = store if store is not None else _lock_store
    acquired = False
    try:
        acquired = await store.try_acquire(name, ttl_seconds)
    except Exception as exc:
        logger.warning("task_lock_acquire_failed_skipping_cycle", name=name, error=str(exc))
        acquired = False
    try:
        yield acquired
    finally:
        if acquired:
            try:
                await store.release(name)
            except Exception as exc:
                logger.warning("task_lock_release_failed", name=name, error=str(exc))


class IdempotencyGuard:
    """Client-supplied ``Idempotency-Key`` replay detector (Redis-backed).

    First use of (scope, user, key) marks the key and returns True; any reuse
    within the TTL returns False so the API layer can answer 409. Distinct keys
    are always allowed. Fail-open: a store outage lets the request through.
    """

    def __init__(self, cache: ICacheService | None = None) -> None:
        self._cache = cache if cache is not None else redis_service

    @staticmethod
    def _key(scope: str, user_id: str, client_key: str) -> str:
        digest = hashlib.sha256(f"{scope}:{user_id}:{client_key}".encode("utf-8")).hexdigest()
        return f"idempotency:{digest}"

    async def check_and_mark(self, scope: str, user_id: str, client_key: str, ttl_seconds: int = 86400) -> bool:
        if not client_key.strip():
            return True
        try:
            return bool(
                await self._cache.set_if_absent(self._key(scope, user_id, client_key), "1", ttl_seconds)
            )
        except Exception as exc:
            logger.warning("idempotency_check_failed_fail_open", scope=scope, error=str(exc))
            return True
