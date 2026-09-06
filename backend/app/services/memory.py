"""
Production Long-Term Memory Service powered by mem0.
Wraps mem0's AsyncMemoryClient to provide:
  - Automatic memory extraction from conversations
  - Semantic deduplication (vector similarity, not just exact-match)
  - Per-user scoped memory namespaces
  - Dynamic relevance-based memory injection into system prompts
  - Conflict resolution: updates contradictory facts automatically
  - Battle-tested at production scale (millions of memories)

Reference: https://docs.mem0.ai/open-source/python-client
"""
from __future__ import annotations

import os
from typing import Any
from uuid import UUID

import structlog

try:
    from mem0 import AsyncMemory, AsyncMemoryClient
except ImportError:
    AsyncMemory = None
    AsyncMemoryClient = None

logger = structlog.get_logger(__name__)


class LongTermMemoryService:
    """
    Production memory service wrapping mem0 AsyncMemory / AsyncMemoryClient.
    All operations are scoped to a user_id namespace.
    Provides automatic graceful degradation if mem0 is not installed.
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

        api_key = os.environ.get("MEM0_API_KEY")
        if api_key and AsyncMemoryClient is not None:
            # Cloud-managed mem0 (recommended for production)
            try:
                self._client = AsyncMemoryClient(api_key=api_key)
                logger.info("mem0_cloud_client_initialized")
            except Exception as e:
                logger.warning("mem0_cloud_init_failed", error=str(e))
        elif AsyncMemory is not None:
            # Self-hosted mem0 with local Qdrant + LLM
            try:
                self._client = AsyncMemory.from_config(
                    config_dict={
                        "vector_store": {
                            "provider": "qdrant",
                            "config": {
                                "host": os.environ.get("QDRANT_HOST", "localhost"),
                                "port": int(os.environ.get("QDRANT_PORT", 6333)),
                                "collection_name": "nexus_user_memories",
                                "embedding_model_dims": 1536,
                            },
                        },
                        "llm": {
                            "provider": "litellm",
                            "config": {
                                "model": os.environ.get(
                                    "MEMORY_EXTRACTION_MODEL", "groq/llama-3.1-8b-instant"
                                ),
                                "temperature": 0.1,
                                "max_tokens": 2000,
                            },
                        },
                        "embedder": {
                            "provider": "openai",
                            "config": {
                                "model": os.environ.get(
                                    "EMBEDDING_MODEL", "text-embedding-ada-002"
                                ),
                            },
                        },
                        "history_db_path": os.environ.get("MEM0_HISTORY_PATH", "mem0_history.db"),
                    }
                )
                logger.info("mem0_self_hosted_initialized_with_qdrant")
            except Exception as e:
                logger.warning("mem0_self_hosted_init_failed", error=str(e))
                self._client = None

    async def add_from_conversation(
        self,
        messages: list[dict[str, str]],
        user_id: str | UUID,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Extract and store memories from a conversation turn.
        """
        if not self._client:
            return []

        uid = str(user_id)
        extra_meta = metadata or {}

        try:
            result = await self._client.add(
                messages=messages,
                user_id=uid,
                metadata={
                    "source": "conversation",
                    "app": "nexus-ai",
                    **extra_meta,
                },
            )
            created = result.get("results", [])
            logger.info(
                "mem0_memories_stored",
                user_id=uid,
                count=len(created),
                events=[r.get("event") for r in created],
            )
            return created

        except Exception as exc:
            logger.warning("mem0_add_failed", user_id=uid, error=str(exc))
            return []

    async def search(
        self,
        query: str,
        user_id: str | UUID,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Semantic search over user memories.
        """
        if not self._client:
            return []

        uid = str(user_id)
        try:
            results = await self._client.search(
                query=query,
                user_id=uid,
                limit=limit,
            )
            logger.debug("mem0_search", user_id=uid, query=query[:50], hits=len(results))
            return results

        except Exception as exc:
            logger.warning("mem0_search_failed", user_id=uid, error=str(exc))
            return []

    async def get_all(
        self,
        user_id: str | UUID,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Retrieve all stored memories for a user.
        """
        if not self._client:
            return []

        uid = str(user_id)
        try:
            return await self._client.get_all(user_id=uid, limit=limit)
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

    async def build_memory_context_block(
        self,
        query: str,
        user_id: str | UUID,
        limit: int = 5,
    ) -> str:
        """
        Builds a formatted context block to inject into the system prompt.
        """
        memories = await self.search(query=query, user_id=user_id, limit=limit)
        if not memories:
            return ""

        memory_lines = [
            f"- {m.get('memory', m.get('text', ''))}"
            for m in memories
            if m.get("memory") or m.get("text")
        ]

        if not memory_lines:
            return ""

        return (
            "### Relevant User Context (from previous conversations):\n"
            + "\n".join(memory_lines)
            + "\n"
        )


long_term_memory = LongTermMemoryService()
