from backend.app.core.logging import logger
from backend.app.infrastructure.cache.redis_client import redis_service

CAG_DEFAULT_TTL = 86400  # 24 hours


class CAGService:
    """
    Cache-Augmented Generation (CAG) Service.
    Caches static system prompts, skill instructions, and reference architectures
    in Redis to avoid redundant disk reads and prompt compilations across runs.
    """
    def __init__(self, cache=redis_service):
        self.cache = cache

    async def get_static_context(self, context_key: str) -> str | None:
        cached = await self.cache.get(f"cag:{context_key}")
        if cached:
            logger.debug("cag_cache_hit", key=context_key)
            return cached
        logger.debug("cag_cache_miss", key=context_key)
        return None

    async def set_static_context(self, context_key: str, content: str, ttl: int = CAG_DEFAULT_TTL) -> None:
        await self.cache.set(f"cag:{context_key}", content, ttl_seconds=ttl)
        logger.debug("cag_cache_set", key=context_key, ttl=ttl)


cag_service = CAGService()
