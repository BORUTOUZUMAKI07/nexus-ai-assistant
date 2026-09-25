import time

import redis.asyncio as redis
from backend.app.core.config import settings
from backend.app.infrastructure.cache.base import ICacheService

# Async Redis client instance. ``protocol=2`` keeps redis-py on RESP2: the
# library's default RESP3 handshake tries ``CLIENT MAINT_NOTIFICATIONS``, which
# Upstash does not implement (logs a noisy 'Command is not available' line on
# every pooled connection).
redis_client: redis.Redis = redis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    max_connections=20,
    protocol=2,
    socket_connect_timeout=2.0,
    socket_timeout=2.0,
)


class RedisService(ICacheService):
    def __init__(self, client: redis.Redis = redis_client):
        self.client = client

    async def get(self, key: str) -> str | None:
        return await self.client.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        if ttl_seconds:
            return await self.client.setex(key, ttl_seconds, value)
        return await self.client.set(key, value)

    async def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        """Atomically write ``key`` only if it does not exist (SETNX + TTL).

        Returns True when the write succeeded (key was absent), False when the
        key is already present. Used for single-use refresh-token guards.
        """
        return bool(await self.client.set(key, value, nx=True, ex=ttl_seconds))

    async def delete(self, key: str) -> int:
        return await self.client.delete(key)

    async def is_token_blacklisted(self, jti: str) -> bool:
        return await self.client.exists(f"blacklist:{jti}") > 0

    async def blacklist_token(self, jti: str, ttl_seconds: int) -> None:
        await self.client.setex(f"blacklist:{jti}", ttl_seconds, "true")

    async def check_rate_limit(self, identifier: str, limit: int = 100, window_seconds: int = 60, cost: int = 1) -> tuple[bool, int]:
        """
        Sliding-window token bucket rate limiter using an atomic Lua script.
        Trimming, counting, and recording happen in ONE server-side op, so two
        racing requests can never both pass the limit. Returns (is_allowed,
        remaining_tokens).
        """
        key = f"rate_limit:{identifier}"
        script = """
        local key = KEYS[1]
        local now = tonumber(ARGV[1])
        local window = tonumber(ARGV[2])
        local limit = tonumber(ARGV[3])
        local cost = tonumber(ARGV[4])
        redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
        local current = redis.call('ZCARD', key)
        if (current + cost) > limit then
            return {0, limit - current}
        end
        for i = 1, cost do
            redis.call('ZADD', key, now + (i - 1) / 1000, now .. ':' .. i)
        end
        redis.call('EXPIRE', key, window)
        return {1, limit - (current + cost)}
        """
        try:
            result = await self.client.eval(script, 1, key, time.time(), window_seconds, limit, cost)
            allowed = bool(result[0])
            remaining = max(0, int(result[1]))
            return allowed, remaining
        except Exception:
            # No pipelined fallback: a non-atomic fallback could double-count.
            # Surface the error so callers can degrade (fail-open) explicitly.
            raise


# Singleton instance for backwards-compatibility
redis_service = RedisService()


def get_cache_service() -> ICacheService:
    """Dependency provider returning the active ICacheService implementation."""
    return redis_service
