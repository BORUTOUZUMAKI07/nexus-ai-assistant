"""
Abstract Cache Service Interface.
Enforces Interface Segregation, Open/Closed, and Dependency Inversion principles for cache and state backends.
"""
from abc import ABC, abstractmethod


class ICacheService(ABC):
    """
    Abstract contract for distributed/in-memory cache providers (Redis, Memcached, MemoryCache).
    """

    @abstractmethod
    async def get(self, key: str) -> str | None:
        """Retrieve a cached string value."""
        pass

    @abstractmethod
    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        """Store a key-value pair with optional time-to-live."""
        pass

    @abstractmethod
    async def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        """Atomically write ``key`` only when it does not already exist.

        Returns True when the write won the race, False when the key was
        already present. This is the primitive behind every "exactly once"
        decision in the app — single-use refresh tokens (AuthService.refresh)
        and the resilience circuit-breaker / rate-limit guards
        (infrastructure.resilience.guards) — all call it through the
        ``ICacheService`` seam, so it must be part of the interface rather than
        an implementation detail of ``RedisService``.
        """
        pass

    @abstractmethod
    async def delete(self, key: str) -> int:
        """Delete a key from cache."""
        pass

    @abstractmethod
    async def is_token_blacklisted(self, jti: str) -> bool:
        """Check if a JWT ID is blacklisted."""
        pass

    @abstractmethod
    async def blacklist_token(self, jti: str, ttl_seconds: int) -> None:
        """Blacklist a JWT ID until its expiry."""
        pass

    @abstractmethod
    async def check_rate_limit(
        self,
        identifier: str,
        limit: int = 100,
        window_seconds: int = 60,
        cost: int = 1,
    ) -> tuple[bool, int]:
        """Check and consume rate limit tokens. Returns (is_allowed, remaining_tokens)."""
        pass
