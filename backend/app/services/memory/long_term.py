"""
Production Long-Term Memory Service powered by mem0.
Wraps mem0's AsyncMemoryClient to provide:
  - Automatic memory extraction from conversations
  - Semantic deduplication (vector similarity, not just exact-match)
  - Per-user and multi-scope (agent, run) memory namespaces
  - Dynamic relevance-based memory injection into system prompts
  - Conflict resolution: updates contradictory facts automatically
  - Resilient PostgreSQL hybrid fallback when mem0 is unavailable
  - Battle-tested at production scale (millions of memories)

Reference: https://docs.mem0.ai/open-source/python-client
"""
from __future__ import annotations

import os
from typing import Any
from uuid import UUID

import structlog
from backend.app.core.config import settings
from backend.app.services.observability.fallbacks import count_fallback

try:
    from mem0 import AsyncMemory, AsyncMemoryClient
except ImportError:
    AsyncMemory = None
    AsyncMemoryClient = None

logger = structlog.get_logger(__name__)


class LongTermMemoryService:
    """
    Production memory service wrapping mem0 AsyncMemory / AsyncMemoryClient.
    All operations are scoped to a user_id namespace with optional agent_id and run_id.
    Provides automatic graceful degradation and resilient PostgreSQL fallback.
    Supports constructor injection of memory client (DIP).
    """

    def __init__(self, client: Any | None = None) -> None:
        if client is not None:
            self._client = client
            return

        self._client = None
        if AsyncMemoryClient is None and AsyncMemory is None:
            logger.info("mem0_not_installed_using_noop_fallback")
            return

        api_key = settings.MEM0_API_KEY
        if api_key and AsyncMemoryClient is not None:
            # Cloud-managed mem0 (recommended for production)
            try:
                self._client = AsyncMemoryClient(api_key=api_key)
                logger.info("mem0_cloud_client_initialized")
            except Exception as e:
                logger.warning("mem0_cloud_init_failed", error=str(e))
        elif AsyncMemory is not None:
            # Self-hosted mem0 with local Qdrant + LLM / dynamic embedder
            try:
                embedder_dims = settings.EMBEDDING_DIMENSION
                if settings.OPENAI_API_KEY:
                    embedder_config = {
                        "provider": "openai",
                        "config": {
                            "model": os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"),
                            "api_key": settings.OPENAI_API_KEY,
                        },
                    }
                    embedder_dims = 1536
                elif settings.GEMINI_API_KEY:
                    embedder_config = {
                        "provider": "gemini",
                        "config": {
                            "model": "models/text-embedding-004",
                            "api_key": settings.GEMINI_API_KEY,
                        },
                    }
                    embedder_dims = 768
                else:
                    embedder_config = {
                        "provider": "huggingface",
                        "config": {
                            "model": "sentence-transformers/all-MiniLM-L6-v2",
                        },
                    }
                    embedder_dims = 384

                self._client = AsyncMemory.from_config(
                    config_dict={
                        "vector_store": {
                            "provider": "qdrant",
                            "config": {
                                "host": settings.QDRANT_HOST,
                                "port": settings.QDRANT_PORT,
                                "collection_name": "nexus_user_memories",
                                "embedding_model_dims": embedder_dims,
                            },
                        },
                        "llm": {
                            "provider": "litellm",
                            "config": {
                                "model": settings.MEMORY_EXTRACTION_MODEL,
                                "temperature": 0.1,
                                "max_tokens": 2000,
                            },
                        },
                        "embedder": embedder_config,
                        "history_db_path": os.environ.get("MEM0_HISTORY_PATH", "mem0_history.db"),
                    }
                )
                logger.info(
                    "mem0_self_hosted_initialized_with_qdrant",
                    embedder=embedder_config["provider"],
                    dims=embedder_dims,
                )
            except Exception as e:
                logger.warning("mem0_self_hosted_init_failed", error=str(e))
                self._client = None

    async def add(
        self,
        content: str,
        user_id: str | UUID,
        agent_id: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """
        Add a single raw memory string directly to mem0 (used for manual creation).
        Returns the created mem0 memory_id, or None if failed/disabled.
        """
        if not self._client:
            return None

        uid = str(user_id)
        extra_meta = metadata or {}
        messages = [{"role": "user", "content": content}]

        kwargs: dict[str, Any] = {
            "messages": messages,
            "user_id": uid,
            "metadata": {
                "source": "manual",
                "app": "nexus-ai",
                **extra_meta,
            },
        }
        if agent_id:
            kwargs["agent_id"] = agent_id
        if run_id:
            kwargs["run_id"] = run_id

        try:
            result = await self._client.add(**kwargs)
            results = result.get("results", []) if isinstance(result, dict) else result
            if results and isinstance(results, list):
                first = results[0]
                mem_id = first.get("id") or first.get("memory_id")
                logger.info("mem0_memory_added_manually", user_id=uid, memory_id=mem_id)
                return str(mem_id) if mem_id else None
            return None
        except Exception as exc:
            logger.warning("mem0_manual_add_failed", user_id=uid, error=str(exc))
            count_fallback("mem0_manual_add")
            return None

    async def add_from_conversation(
        self,
        messages: list[dict[str, str]],
        user_id: str | UUID,
        metadata: dict[str, Any] | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Extract and store memories from a conversation turn with multi-scope support.
        """
        if not self._client:
            return []

        uid = str(user_id)
        extra_meta = metadata or {}

        kwargs: dict[str, Any] = {
            "messages": messages,
            "user_id": uid,
            "metadata": {
                "source": "conversation",
                "app": "nexus-ai",
                **extra_meta,
            },
        }
        if agent_id:
            kwargs["agent_id"] = agent_id
        if run_id:
            kwargs["run_id"] = run_id

        try:
            result = await self._client.add(**kwargs)
            created = result.get("results", []) if isinstance(result, dict) else result
            if not isinstance(created, list):
                created = []
            logger.info(
                "mem0_memories_stored",
                user_id=uid,
                count=len(created),
                events=[r.get("event") for r in created if isinstance(r, dict)],
            )
            return created

        except Exception as exc:
            logger.warning("mem0_add_failed", user_id=uid, error=str(exc))
            count_fallback("mem0_add")
            return []

    async def search(
        self,
        query: str,
        user_id: str | UUID,
        limit: int = 10,
        agent_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Semantic search over user memories with optional multi-scope filtering.
        """
        if not self._client:
            return []

        uid = str(user_id)
        filters: dict[str, Any] = {"user_id": uid}
        if agent_id:
            filters["agent_id"] = agent_id
        if run_id:
            filters["run_id"] = run_id

        try:
            results = await self._client.search(
                query=query,
                filters=filters,
                top_k=limit,
            )
            if isinstance(results, dict):
                results = results.get("results", [])
            logger.debug("mem0_search", user_id=uid, query=query[:50], hits=len(results))
            return results

        except Exception as exc:
            logger.warning("mem0_search_failed", user_id=uid, error=str(exc))
            count_fallback("mem0_search")
            return []

    async def get_all(
        self,
        user_id: str | UUID,
        limit: int = 50,
        *,
        all_pages: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Retrieve stored memories for a user, optionally across every page.

        ``limit`` is the page size. ``all_pages`` is for completeness-sensitive
        reads such as GDPR export: mem0 returns a paginated ``next`` token, so a
        single call would silently drop facts past the first page. Fail-open is
        unchanged — any error yields an empty list rather than breaking a read.
        """
        if not self._client:
            return []

        uid = str(user_id)
        page = 1
        seen_next: set[str] = set()
        collected: list[dict[str, Any]] = []
        try:
            while True:
                data = await self._client.get_all(
                    filters={"user_id": uid}, page=page, page_size=limit
                )
                if isinstance(data, dict):
                    results = data.get("results", [])
                    next_page = data.get("next")
                else:
                    results = data
                    next_page = None
                if not isinstance(results, list):
                    results = []
                if not all_pages:
                    return results

                collected.extend(results)
                token = str(next_page) if next_page else ""
                # A repeated token or an empty page with a token would otherwise
                # spin forever if the upstream client ignored `page`.
                if not token or token in seen_next or not results:
                    break
                seen_next.add(token)
                page += 1
            return collected
        except Exception as exc:
            logger.warning("mem0_get_all_failed", user_id=uid, error=str(exc))
            return []

    async def delete(self, memory_id: str) -> bool:
        """
        Delete a single memory by its mem0 memory_id.
        """
        if not self._client:
            return True

        try:
            await self._client.delete(memory_id=memory_id)
            logger.info("mem0_memory_deleted", memory_id=memory_id)
            return True
        except Exception as exc:
            logger.warning("mem0_delete_failed", memory_id=memory_id, error=str(exc))
            return False

    async def delete_all_for_user(self, user_id: str | UUID) -> bool:
        """
        Delete all memories for a user (GDPR Article 17 erasure).
        """
        if not self._client:
            return True

        uid = str(user_id)
        try:
            if hasattr(self._client, "delete_all"):
                await self._client.delete_all(user_id=uid)
            elif hasattr(self._client, "delete_users"):
                await self._client.delete_users(user_id=uid)
            logger.info("mem0_all_user_memories_deleted", user_id=uid)
            return True
        except Exception as exc:
            logger.warning("mem0_delete_all_failed", user_id=uid, error=str(exc))
            return False

    async def get_history(self, memory_id: str) -> list[dict[str, Any]]:
        """
        Retrieve audit update history for a single memory.
        """
        if not self._client or not hasattr(self._client, "history"):
            return []

        try:
            res = await self._client.history(memory_id=memory_id)
            if isinstance(res, dict):
                return res.get("results", [])
            return res if isinstance(res, list) else []
        except Exception as exc:
            logger.warning("mem0_history_failed", memory_id=memory_id, error=str(exc))
            return []

    async def get_fallback_memories(
        self,
        user_id: str | UUID,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """
        Fallback to PostgreSQL user_memories when mem0 is unavailable or returns empty.
        Fetches the user's active memories ordered by confidence and recency.
        """
        from backend.app.domain.user.models import UserMemory
        from backend.app.infrastructure.database.session import async_session_factory
        from sqlmodel import select

        try:
            uid = UUID(str(user_id))
        except (ValueError, TypeError, AttributeError):
            return []

        try:
            async with async_session_factory() as session:
                statement = (
                    select(UserMemory)
                    .where(
                        UserMemory.user_id == uid,
                        UserMemory.is_active == True,  # noqa: E712
                        UserMemory.scope == "user",
                    )
                    .order_by(UserMemory.confidence.desc(), UserMemory.updated_at.desc())
                    .limit(limit)
                )
                result = await session.exec(statement)
                rows = list(result.all())
                return [
                    {
                        "id": str(r.mem0_id or r.id),
                        "db_id": str(r.id),
                        "memory": r.content,
                        "category": r.category,
                        "confidence": r.confidence,
                    }
                    for r in rows
                ]
        except Exception as exc:
            logger.warning("memory_sql_fallback_failed", user_id=str(user_id), error=str(exc))
            count_fallback("mem0_sql_fallback")
            return []

    async def build_memory_context_block_with_ids(
        self,
        query: str,
        user_id: str | UUID,
        limit: int = 8,
        agent_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        Builds context block and returns list of memory items (including IDs)
        for prompt injection and reinforcement.
        """
        memories = await self.search(
            query=query,
            user_id=user_id,
            limit=limit,
            agent_id=agent_id,
            run_id=run_id,
        )

        # Resilient hybrid fallback: if mem0 returns no memories, load active from PostgreSQL
        if not memories:
            memories = await self.get_fallback_memories(user_id=user_id, limit=limit)

        if not memories:
            return "", []

        memory_lines = [
            f"- {m.get('memory', m.get('text', ''))}"
            for m in memories
            if m.get("memory") or m.get("text")
        ]

        if not memory_lines:
            return "", []

        block = (
            "### Relevant User Context (from previous conversations):\n"
            + "\n".join(memory_lines)
            + "\n"
        )
        return block, memories

    async def build_memory_context_block(
        self,
        query: str,
        user_id: str | UUID,
        limit: int = 5,
        agent_id: str | None = None,
        run_id: str | None = None,
    ) -> str:
        """
        Builds a formatted context block to inject into the system prompt.
        """
        block, _ = await self.build_memory_context_block_with_ids(
            query=query,
            user_id=user_id,
            limit=limit,
            agent_id=agent_id,
            run_id=run_id,
        )
        return block


long_term_memory = LongTermMemoryService()
