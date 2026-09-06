"""
Prompt Compiler Service.
Applies Attention Budgeting, Static Prefix Caching Anchors,
and Dynamic Skill & Memory Injection.
"""
import hashlib
import json

import structlog
from backend.app.domain.prompt.models import Skill
from backend.app.domain.user.models import UserMemory
from backend.app.infrastructure.cache.cag_service import cag_service

logger = structlog.get_logger(__name__)

SYSTEM_BASE_PROMPT = """You are Nexus AI, an advanced, highly capable, and transparent AI assistant.
You strictly adhere to factual accuracy, domain expertise, and rigorous logic.
Always provide well-structured, clear, and comprehensive explanations.
When code or data analysis is required, use available tools and interpret execution outputs meticulously.
When citations are available, ground your answers directly in the retrieved evidence."""


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
        sections: list[str] = []

        # 1. Static Base Prompt Anchor (Cache-friendly prefix)
        sections.append(f"# Core Directive\n{self.base_prompt}")

        # 2. Custom Instructions (if any)
        if custom_instructions and custom_instructions.strip():
            sections.append(f"# Custom Instructions\n{custom_instructions.strip()}")

        # 3. Active Skills Instructions
        if active_skills:
            skill_text = "\n\n".join(
                [
                    f"## Skill: {s.name}\n{getattr(s, 'content', getattr(s, 'instructions', ''))}"
                    for s in active_skills
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
        CAG-backed variant of compile_system_prompt. Only static inputs
        (no per-user memories, no dynamic retrieved context) are worth caching:
        the compiled prompt is keyed on a stable hash and served from Redis on
        later compilations, avoiding redundant string building across runs.
        Falls back to an uncached compile when Redis is unavailable.
        """
        is_static = not user_memories and not retrieved_context
        cache_key = ""
        if is_static:
            fingerprint = {
                "base": self.base_prompt,
                "custom_instructions": custom_instructions,
                "active_skills": [
                    {"name": s.name, "content": getattr(s, "content", getattr(s, "instructions", ""))}
                    for s in (active_skills or [])
                    if s.is_enabled
                ],
            }
            cache_key = "cag:prompt:" + hashlib.sha256(
                json.dumps(fingerprint, default=str, sort_keys=True).encode("utf-8")
            ).hexdigest()

        if cache_key:
            try:
                cached = await cag_service.get_static_context(cache_key)
                if cached:
                    logger.debug("cag_prompt_cache_hit")
                    return cached
            except Exception as exc:
                logger.warning("cag_prompt_cache_read_failed", error=str(exc))

        compiled = self.compile_system_prompt(
            custom_instructions=custom_instructions,
            active_skills=active_skills,
            user_memories=user_memories,
            retrieved_context=retrieved_context,
        )

        if cache_key:
            try:
                await cag_service.set_static_context(cache_key, compiled)
                logger.debug("cag_prompt_cache_set")
            except Exception as exc:
                logger.warning("cag_prompt_cache_write_failed", error=str(exc))

        return compiled


prompt_compiler = PromptCompiler()
