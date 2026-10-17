"""
Semantic Response Cache.

The exact-match `Infrastructure.ResponseCache` only hits on a byte-identical
normalized query ("what is the capital of France?" vs "France's capital"). This
layer catches paraphrases by embedding the query and comparing against recently
cached queries for the same (user, model, prompt_variant) bucket.

Off by default (`RESPONSE_CACHE_SEMANTIC_ENABLED`) because it adds one
embedding call per request on the query path. It is a measured trade-off, not a
default benefit.

Fail-open in every direction: no embedder, embedding error, timeout, malformed
store, or below-threshold similarity -> miss, never an exception. Per-user
bucketing means one user's cache can never serve another user's answer.
"""
from __future__ import annotations

import asyncio
import json
import math
from typing import Any

import structlog

from app.core.config import settings
from app.infrastructure.cache.redis_client import redis_service

logger = structlog.get_logger(__name__)

_PREFIX = "response_cache:sem:v1:"


def _normalize_query(text: str) -> str:
    from app.infrastructure.cache.response_cache import normalize_query

    return normalize_query(text)


def _bucket_key(user_id: str, model: str, prompt_variant: str = "default") -> str:
    return f"{_PREFIX}{user_id}:{model}:{prompt_variant}"


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _resolve_embedder():
    try:
        from app.services.rag.retrieval import retrieval_service

        return retrieval_service
    except Exception as exc:  # pragma: no cover - lazy import environment fault
        logger.warning(
            "semantic_cache_embedder_unavailable",
            error_type=type(exc).__name__,
        )
        return None


class SemanticResponseCache:
    def __init__(
        self,
        cache=redis_service,
        embedder=None,
    ) -> None:
        self._cache = cache
        self._embedder = embedder

    def _get_embedder(self):
        if self._embedder is not None:
            return self._embedder
        embedder = _resolve_embedder()
        self._embedder = embedder
        return embedder

    async def _embed(self, text: str) -> list[float] | None:
        embedder = self._get_embedder()
        if embedder is None:
            return None
        gen = getattr(embedder, "generate_embedding", None)
        if gen is None:
            return None
        try:
            vector = await asyncio.wait_for(
                gen(text),
                timeout=settings.RESPONSE_CACHE_SEMANTIC_EMBED_TIMEOUT_SECONDS,
            )
            if not isinstance(vector, list):
                return None
            return vector
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "semantic_cache_embed_failed_fail_open",
                error_type=type(exc).__name__,
            )
            return None

    async def _load(self, user_id: str, model: str, prompt_variant: str) -> list[dict[str, Any]]:
        key = _bucket_key(user_id, model, prompt_variant)
        try:
            raw = await self._cache.get(key)
        except Exception as exc:
            logger.warning(
                "semantic_cache_load_failed_fail_open",
                error_type=type(exc).__name__,
            )
            return []
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        out: list[dict[str, Any]] = []
        for e in data:
            if isinstance(e, dict):
                out.append(e)
        return out

    async def _store(
        self,
        user_id: str,
        model: str,
        prompt_variant: str,
        entries: list[dict[str, Any]],
    ) -> None:
        key = _bucket_key(user_id, model, prompt_variant)
        payload = json.dumps(entries, default=str)
        try:
            await self._cache.set(key, payload, ttl_seconds=settings.RESPONSE_CACHE_TTL_SECONDS)
        except Exception as exc:
            logger.warning(
                "semantic_cache_store_failed_fail_open",
                error_type=type(exc).__name__,
            )

    async def get(
        self,
        user_id: str,
        model: str,
        query: str,
        prompt_variant: str = "default",
    ) -> dict[str, Any] | None:
        if not settings.RESPONSE_CACHE_ENABLED or not settings.RESPONSE_CACHE_SEMANTIC_ENABLED:
            return None
        normalized = _normalize_query(query)
        if len(normalized) < settings.RESPONSE_CACHE_MIN_LENGTH:
            return None
        vector = await self._embed(normalized)
        if not vector:
            return None
        entries = await self._load(user_id, model, prompt_variant)
        if not entries:
            return None
        best: dict[str, Any] | None = None
        best_sim = 0.0
        for e in entries:
            emb = e.get("embedding") or []
            sim = _cosine(vector, emb)
            if sim > best_sim:
                best_sim = sim
                best = e
            if best_sim >= settings.RESPONSE_CACHE_SEMANTIC_THRESHOLD + 1e-6:
                break
        if best is None or best_sim < settings.RESPONSE_CACHE_SEMANTIC_THRESHOLD:
            return None
        content = best.get("content")
        if not content:
            return None
        payload: dict[str, Any] = {
            "content": content,
            "tokens_input": int(best.get("tokens_input") or 0),
            "tokens_output": int(best.get("tokens_output") or 0),
            "cost_usd": float(best.get("cost_usd") or 0.0),
        }
        logger.debug(
            "semantic_response_cache_hit",
            user_id=user_id,
            model=model,
            similarity=round(best_sim, 4),
        )
        return payload

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
        if not (settings.RESPONSE_CACHE_ENABLED and settings.RESPONSE_CACHE_SEMANTIC_ENABLED):
            return
        normalized = _normalize_query(query)
        if len(normalized) < settings.RESPONSE_CACHE_MIN_LENGTH:
            return
        if not content or not content.strip():
            return
        vector = await self._embed(normalized)
        if not vector:
            return
        entries = await self._load(user_id, model, prompt_variant)
        entries = [e for e in entries if e.get("query") != normalized]
        entries.insert(
            0,
            {
                "query": normalized,
                "embedding": vector,
                "content": content,
                "tokens_input": int(tokens_input or 0),
                "tokens_output": int(tokens_output or 0),
                "cost_usd": float(cost_usd or 0.0),
            },
        )
        max_e = settings.RESPONSE_CACHE_SEMANTIC_MAX_ENTRIES
        if len(entries) > max_e:
            entries = entries[:max_e]
        await self._store(user_id, model, prompt_variant, entries)


semantic_response_cache = SemanticResponseCache()
