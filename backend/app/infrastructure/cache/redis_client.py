import time

import redis.asyncio as redis
from backend.app.core.config import settings
from backend.app.infrastructure.cache.base import ICacheService

# Async Redis client instance
redis_client: redis.Redis = redis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    max_connections=20
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

    async def delete(self, key: str) -> int:
        return await self.client.delete(key)

    async def is_token_blacklisted(self, jti: str) -> bool:
        return await self.client.exists(f"blacklist:{jti}") > 0

    async def blacklist_token(self, jti: str, ttl_seconds: int) -> None:
        await self.client.setex(f"blacklist:{jti}", ttl_seconds, "true")

    async def check_rate_limit(self, identifier: str, limit: int = 100, window_seconds: int = 60, cost: int = 1) -> tuple[bool, int]:
        """
        Sliding window token bucket rate limiter using Redis sorted sets (ZADD/ZREMRANGEBYSCORE).
        Returns (is_allowed, remaining_tokens).
        """
        now = time.time()
        clear_before = now - window_seconds
        key = f"rate_limit:{identifier}"

        pipe = self.client.pipeline()
        # Remove requests older than the sliding window
        pipe.zremrangebyscore(key, 0, clear_before)
        # Get count of current requests in window
        pipe.zcard(key)
        results = await pipe.execute()
        current_count = results[1]

        if current_count + cost > limit:
            return False, max(0, limit - current_count)

        # Record this request
        pipe = self.client.pipeline()
        for i in range(cost):
            pipe.zadd(key, {f"{now}-{i}": now})
        pipe.expire(key, window_seconds)
        await pipe.execute()

        return True, max(0, limit - (current_count + cost))


# Singleton instance for backwards-compatibility
redis_service = RedisService()


def get_cache_service() -> ICacheService:
    """Dependency provider returning the active ICacheService implementation."""
    return redis_service
