"""
Response Cache (MD §8.5 cost & efficiency — caching predictions keyed by
input hash).

Exact-normalized-query response cache for the synchronous chat path: repeated,
effectively-identical questions skip the LLM round-trip entirely (latency +
token cost). Keyed per user+model so one user's cache can never leak another's
content, TTL-bounded, and strictly fail-open — a cache error must never break a
chat request.

Deliberately deterministic (normalized exact-match), not embedding-based: the
embedding-semantic variant would add a vector round-trip on every query and is
left as a documented extension point.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog
from backend.app.core.config import settings
from backend.app.infrastructure.cache.redis_client import redis_service

logger = structlog.get_logger(__name__)

_PREFIX = "response_cache:v1:"

_PUNCTUATION_STRIP = "?!.,;:"


def normalize_query(text: str) -> str:
    """Canonical form: lowercase, whitespace-collapsed, punctuation-noise-stripped.

    Tokens are re-joined after stripping trailing punctuation so
    ``"France?"`` and ``"france ?"`` resolve to the same cache key.
    """
    tokens = [t.strip(_PUNCTUATION_STRIP) for t in text.strip().lower().split()]
    tokens = [t for t in tokens if t]
    return " ".join(tokens)


def cache_key(user_id: str, model: str, query: str, prompt_variant: str = "default") -> str:
    # Normalize defensively (idempotent) so raw and pre-normalized queries hash identically.
    digest = hashlib.sha256(normalize_query(query).encode("utf-8")).hexdigest()
    # prompt_variant scopes the key so a bandit/canary variant flip can never
    # serve an answer generated under a different system prompt.
    return f"{_PREFIX}{user_id}:{model}:{prompt_variant}:{digest}"


class ResponseCache:
    def __init__(self, cache=None):
        self._cache = cache if cache is not None else redis_service

    async def get(
        self,
        user_id: str,
        model: str,
        query: str,
        prompt_variant: str = "default",
    ) -> dict[str, Any] | None:
        if not settings.RESPONSE_CACHE_ENABLED:
            return None
        normalized = normalize_query(query)
        if len(normalized) < settings.RESPONSE_CACHE_MIN_LENGTH:
            return None
        try:
            raw = await self._cache.get(cache_key(user_id, model, normalized, prompt_variant))
            if not raw:
                return None
            payload = json.loads(raw)
            if not isinstance(payload, dict) or "content" not in payload:
                return None
            logger.debug("response_cache_hit", user_id=user_id, model=model, prompt_variant=prompt_variant)
            return payload
        except Exception as exc:
            logger.warning("response_cache_read_failed_fail_open", error=str(exc))
            return None

    async def set(
        self,
        user_id: str,
        model: str,
        query: str,
        content: str,
        tokens_input: int,
        tokens_output: int,
        cost_usd: float,
        prompt_variant: str = "default",
    ) -> None:
        if not settings.RESPONSE_CACHE_ENABLED:
            return
        normalized = normalize_query(query)
        if len(normalized) < settings.RESPONSE_CACHE_MIN_LENGTH:
            return
        if not content.strip():
            return
        try:
            payload = json.dumps(
                {
                    "content": content,
                    "tokens_input": tokens_input,
                    "tokens_output": tokens_output,
                    "cost_usd": cost_usd,
                }
            )
            await self._cache.set(
                cache_key(user_id, model, normalized, prompt_variant),
                payload,
                ttl_seconds=settings.RESPONSE_CACHE_TTL_SECONDS,
            )
        except Exception as exc:
            logger.warning("response_cache_write_failed_fail_open", error=str(exc))


response_cache = ResponseCache()
