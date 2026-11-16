"""
Prompt Application Service.
Owns prompt template and skill management use cases (SRP).
"""
from uuid import UUID

import structlog
from backend.app.domain.prompt.models import Skill
from backend.app.domain.prompt.repository import PromptRepository
from backend.app.domain.prompt.schemas import PromptTemplateCreate
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)

#: Starter templates materialised for a user whose library is still empty.
#:
#: The library shipped with endpoints and a UI but no way to get a first row:
#: `prompt_templates.user_id` is NOT NULL, so a shared/system template could not
#: be inserted, and nothing ever created a per-user one. Every deployment
#: therefore served an empty list and the composer's "Saved templates" group
#: could never appear. These are seeded per user on first read instead of adding
#: a system-owned row, which would need a migration on a NOT NULL column.
_STARTER_TEMPLATES: tuple[dict[str, object], ...] = (
    {
        "title": "Senior code review",
        "category": "engineering",
        "system_prompt": (
            "You are a senior engineer reviewing code. Report correctness bugs, "
            "security issues and performance problems in that order, each with the "
            "line it applies to and a concrete fix."
        ),
        "user_prompt_template": (
            "Review this code for correctness, security and performance:\n\n```\n{{code}}\n```"
        ),
        "input_variables": ["code"],
    },
    {
        "title": "Deep research brief",
        "category": "research",
        "system_prompt": (
            "You are a research analyst. Produce a structured brief: what is known, "
            "what is contested, what is unknown, and the sources behind each claim."
        ),
        "user_prompt_template": (
            "Write a research brief on {{topic}}. Separate established findings from "
            "open questions and cite sources."
        ),
        "input_variables": ["topic"],
    },
    {
        "title": "Root-cause a failure",
        "category": "debugging",
        "system_prompt": (
            "You diagnose failures. State the most likely cause first, then the "
            "evidence for it, then the cheapest experiment that would confirm or "
            "refute it."
        ),
        "user_prompt_template": (
            "Here is the error and the surrounding context. What is the root cause?\n\n"
            "{{error}}"
        ),
        "input_variables": ["error"],
    },
    {
        "title": "Explain like a staff engineer",
        "category": "general",
        "system_prompt": (
            "You explain technical topics to an experienced engineer: no basics, no "
            "analogies, trade-offs and failure modes front and centre."
        ),
        "user_prompt_template": (
            "Explain {{topic}} to a staff engineer: the mental model, the trade-offs, "
            "and where it breaks."
        ),
        "input_variables": ["topic"],
    },
)


class PromptService:
    """
    Application service for prompt template and skill use cases.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = PromptRepository(session)

    async def list_templates(self, user_id: UUID, include_public: bool = True):
        templates = await self._repo.get_templates(
            user_id=user_id, include_public=include_public
        )
        # Emptiness is about the user's OWN library, not the listing. Another
        # account's public template makes the combined result non-empty while
        # this user still has nothing of their own, which is exactly the account
        # that needs seeding.
        if await self._repo.get_templates(user_id=user_id, include_public=False):
            return templates
        # First look, empty library: materialise the starter set so the feature is
        # usable on a fresh account. Fail-open — a seed failure must not turn the
        # prompt library endpoint into an error.
        try:
            for starter in _STARTER_TEMPLATES:
                await self._repo.create_template(
                    user_id=user_id,
                    title=str(starter["title"]),
                    category=str(starter["category"]),
                    system_prompt=str(starter["system_prompt"]),
                    user_prompt_template=str(starter["user_prompt_template"]),
                    input_variables=list(starter["input_variables"]),  # type: ignore[arg-type]
                    is_public=False,
                )
            templates = await self._repo.get_templates(
                user_id=user_id, include_public=include_public
            )
            logger.info("prompt_starters_seeded", user_id=str(user_id), count=len(templates))
        except Exception as exc:
            logger.warning("prompt_starter_seed_failed", error=type(exc).__name__)
        return templates

    async def create_template(self, user_id: UUID, template_in: PromptTemplateCreate):
        return await self._repo.create_template(
            user_id=user_id,
            title=template_in.title,
            category=template_in.category,
            system_prompt=template_in.system_prompt,
            user_prompt_template=template_in.user_prompt_template,
            input_variables=template_in.input_variables,
            is_public=template_in.is_public,
        )

    async def list_skills(self, enabled_only: bool = True):
        return await self._repo.get_skills(enabled_only=enabled_only)


async def load_active_skills() -> list[Skill]:
    """Load the enabled ``skills`` rows for injection into a system prompt.

    Session-owning convenience wrapper so callers on the request hot path do not
    each have to build a repository. This is the writer that was missing for
    ``AgentState.active_skills``: the ``skills`` table, the REST API and the
    injection in ``prompt_compiler`` all existed, but nothing ever read the table
    at request time, so skill markdown never reached a live prompt.

    Fail-open by design — a database blip must degrade the prompt to "no extra
    skills", never fail the user's chat turn. Returns ``[]`` on any error.
    """
    from backend.app.infrastructure.database.session import async_session_factory

    try:
        async with async_session_factory() as session:
            return list(await PromptRepository(session).get_skills(enabled_only=True))
    except Exception as exc:
        logger.warning("active_skills_load_failed", error=str(exc))
        return []
