"""
Prompt Compiler Service.
Applies Attention Budgeting, Static Prefix Caching Anchors,
and Dynamic Skill & Memory Injection.
"""
import hashlib
import json
from pathlib import Path

import structlog
from backend.app.core.config import settings
from backend.app.domain.prompt.models import Skill
from backend.app.domain.user.models import UserMemory
from backend.app.infrastructure.cache.static_context_cache import static_context_cache

logger = structlog.get_logger(__name__)

def load_prompt(name: str) -> str:
    """Load a prompt template text file from the configured prompt directory.
    The file should be named `<name>.txt`.
    """
    file_path = Path(settings.PROMPT_DIR) / f"{name}.txt"
    try:
        return file_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.error("prompt_load_failed", name=name, error=str(exc))
        return ""

# Load the base system prompt from `system_prompt.txt`
SYSTEM_BASE_PROMPT = load_prompt("system_prompt")


class PromptCompiler:
    """
    Compiles system instructions, memory context, and skill directives
    into a cache-optimized prompt structure.
    Static prefixes are ordered first to maximize KV Cache hits across providers.
    """

    def __init__(self, base_prompt: str = SYSTEM_BASE_PROMPT):
        self.base_prompt = base_prompt

    def compile_system_prompt(
        self,
        custom_instructions: str | None = None,
        active_skills: list[Skill] | None = None,
        user_memories: list[UserMemory] | None = None,
        retrieved_context: str | None = None,
    ) -> str:
        from backend.app.services.static_corpus import static_corpus_service

        sections: list[str] = []

        # 1. Static Base Prompt Anchor (Cache-friendly prefix)
        sections.append(f"# Core Directive\n{self.base_prompt}")

        # 1a. Static corpus (opt-in, CAG-lite) -- appended as a stable prefix
        # segment so that, if enabled, it remains cache-friendly and injects a
        # fixed shared knowledge base. On free tiers this only helps via
        # automatic provider prefix caching, not explicit context caching.
        corpus = static_corpus_service.build_prefix()
        if corpus:
            sections.append(corpus)

        # 2. Custom Instructions (if any)
        if custom_instructions and custom_instructions.strip():
            sections.append(f"# Custom Instructions\n{custom_instructions.strip()}")

        # 3. Active Skills Instructions
        if active_skills:
            # Deterministic ordering for byte-stable prefixes (cache hits)
            skills = sorted(active_skills, key=lambda s: (s.name or ""))
            skill_text = "\n\n".join(
                [
                    f"## Skill: {s.name}\n{getattr(s, 'content', getattr(s, 'instructions', ''))}"
                    for s in skills
                    if s.is_enabled
                ]
            )
            if skill_text:
                sections.append(f"# Active Skills & Capabilities\n{skill_text}")

        # 4. User Long-Term Memories (Personalization)
        if user_memories:
            mem_items = "\n".join([f"- [{m.category}] {m.content}" for m in user_memories if m.is_active])
            if mem_items:
                sections.append(f"# User Context & Persistent Preferences\n{mem_items}")

        # 5. Retrieved Document Context (Dynamic grounding)
        if retrieved_context and retrieved_context.strip():
            sections.append(f"# Grounding Evidence & Reference Context\n{retrieved_context.strip()}")

        compiled = "\n\n---\n\n".join(sections)
        logger.debug("system_prompt_compiled", total_length=len(compiled), sections_count=len(sections))
        return compiled

    async def compile_system_prompt_cached(
        self,
        custom_instructions: str | None = None,
        active_skills: list[Skill] | None = None,
        user_memories: list[UserMemory] | None = None,
        retrieved_context: str | None = None,
    ) -> str:
        """
        Cache-backed variant of compile_system_prompt. Only static inputs
        (no per-user memories, no dynamic retrieved context) are worth caching:
        the compiled prompt is keyed on a stable hash and served from Redis on
        later compilations, avoiding redundant string building across runs.
        Falls back to an uncached compile when Redis is unavailable.
        """
        is_static = not user_memories and not retrieved_context
        cache_key = ""
        if is_static:
            from backend.app.services.static_corpus import static_corpus_service

            fingerprint = {
                "base": self.base_prompt,
                "custom_instructions": custom_instructions,
                "static_corpus_fp": static_corpus_service._fingerprint(
                    __import__("pathlib").Path(settings.STATIC_CORPUS_PATH or ".")
                )[0]
                if (settings.STATIC_CORPUS_ENABLED and settings.STATIC_CORPUS_PATH)
                else "",
                "active_skills": [
                    {"name": s.name, "content": getattr(s, "content", getattr(s, "instructions", ""))}
                    for s in (sorted(active_skills or [], key=lambda s: (s.name or "")))
                    if s.is_enabled
                ],
            }
            cache_key = "prompt:" + hashlib.sha256(
                json.dumps(fingerprint, default=str, sort_keys=True).encode("utf-8")
            ).hexdigest()

        if cache_key:
            try:
                cached = await static_context_cache.get_static_context(cache_key)
                if cached:
                    logger.debug("prompt_cache_hit")
                    return cached
            except Exception as exc:
                logger.warning("prompt_cache_read_failed", error=str(exc))

        compiled = self.compile_system_prompt(
            custom_instructions=custom_instructions,
            active_skills=active_skills,
            user_memories=user_memories,
            retrieved_context=retrieved_context,
        )

        if cache_key:
            try:
                await static_context_cache.set_static_context(cache_key, compiled)
                logger.debug("prompt_cache_set")
            except Exception as exc:
                logger.warning("prompt_cache_write_failed", error=str(exc))

        return compiled


prompt_compiler = PromptCompiler()
