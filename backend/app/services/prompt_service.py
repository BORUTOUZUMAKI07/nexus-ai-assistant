"""
Prompt Application Service.
Owns prompt template and skill management use cases (SRP).
"""
from uuid import UUID

import structlog
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
