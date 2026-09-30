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


class PromptService:
    """
    Application service for prompt template and skill use cases.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = PromptRepository(session)

    async def list_templates(self, user_id: UUID, include_public: bool = True):
        return await self._repo.get_templates(
            user_id=user_id, include_public=include_public
        )

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
