from backend.app.core.logging import logger
from backend.app.infrastructure.cache.redis_client import redis_service

STATIC_CONTEXT_DEFAULT_TTL = 86400  # 24 hours


class StaticContextCache:
    """
    Cache for *static compiled context* -- compiled system-prompt strings keyed
    by a stable hash of their static inputs (base prompt, custom instructions,
    enabled skills). Serves the same string from Redis on later compilations,
    avoiding redundant template reads and string assembly across runs.

    This is NOT Cache-Augmented Generation and NOT a KV cache: it stores no
    model attention state and never lets a request skip retrieval. It is a
    prompt-string cache, and it is named accordingly so its behaviour is not
    overstated.
    """

    def __init__(self, cache=redis_service):
        self.cache = cache

    async def get_static_context(self, context_key: str) -> str | None:
        cached = await self.cache.get(f"static_context:{context_key}")
        if cached:
            logger.debug("static_context_cache_hit", key=context_key)
            return cached
        logger.debug("static_context_cache_miss", key=context_key)
        return None

    async def set_static_context(
        self, context_key: str, content: str, ttl: int = STATIC_CONTEXT_DEFAULT_TTL
    ) -> None:
        await self.cache.set(f"static_context:{context_key}", content, ttl_seconds=ttl)
        logger.debug("static_context_cache_set", key=context_key, ttl=ttl)


static_context_cache = StaticContextCache()
