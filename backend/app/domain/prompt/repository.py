"""
Repository for Prompt and Skill domain operations.
"""
from datetime import datetime
from uuid import UUID

from backend.app.domain.base_repository import BaseRepository
from backend.app.domain.prompt.models import PromptTemplate, PromptVersion, Skill
from sqlmodel import or_, select
from sqlmodel.ext.asyncio.session import AsyncSession


class PromptRepository(BaseRepository[PromptTemplate]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, PromptTemplate)

    async def get_templates(self, user_id: UUID, include_public: bool = True) -> list[PromptTemplate]:
        statement = select(PromptTemplate)
        if include_public:
            statement = statement.where(or_(PromptTemplate.user_id == user_id, PromptTemplate.is_public == True))
        else:
            statement = statement.where(PromptTemplate.user_id == user_id)
        statement = statement.order_by(PromptTemplate.updated_at.desc())
        result = await self.session.exec(statement)
        return list(result.all())

    async def get_template_by_id(self, template_id: UUID) -> PromptTemplate | None:
        statement = select(PromptTemplate).where(PromptTemplate.id == template_id)
        result = await self.session.exec(statement)
        return result.first()

    async def create_template(
        self,
        user_id: UUID,
        title: str,
        category: str,
        system_prompt: str,
        user_prompt_template: str | None = None,
        input_variables: list[str] | None = None,
        is_public: bool = False,
    ) -> PromptTemplate:
        template = PromptTemplate(
            user_id=user_id,
            title=title,
            category=category,
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
            input_variables=input_variables or [],
            is_public=is_public,
            version=1,
        )
        self.session.add(template)
        await self.session.commit()
        await self.session.refresh(template)

        # Store initial version
        version = PromptVersion(
            template_id=template.id,
            version=1,
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
            created_by=user_id,
            change_summary="Initial creation",
        )
        self.session.add(version)
        await self.session.commit()

        return template

    async def update_template(
        self,
        template: PromptTemplate,
        update_data: dict,
        user_id: UUID,
        change_summary: str | None = None,
    ) -> PromptTemplate:
        system_prompt_changed = "system_prompt" in update_data and update_data["system_prompt"] != template.system_prompt
        user_template_changed = "user_prompt_template" in update_data and update_data["user_prompt_template"] != template.user_prompt_template

        for key, value in update_data.items():
            if value is not None and hasattr(template, key):
                setattr(template, key, value)

        if system_prompt_changed or user_template_changed:
            template.version += 1
            version = PromptVersion(
                template_id=template.id,
                version=template.version,
                system_prompt=template.system_prompt,
                user_prompt_template=template.user_prompt_template,
                created_by=user_id,
                change_summary=change_summary or f"Version {template.version} update",
            )
            self.session.add(version)

        template.updated_at = datetime.utcnow()
        self.session.add(template)
        await self.session.commit()
        await self.session.refresh(template)
        return template

    # Skills
    async def get_skills(self, enabled_only: bool = True) -> list[Skill]:
        statement = select(Skill)
        if enabled_only:
            statement = statement.where(Skill.is_enabled == True)
        result = await self.session.exec(statement)
        return list(result.all())

    async def get_skill_by_name(self, name: str) -> Skill | None:
        statement = select(Skill).where(Skill.name == name)
        result = await self.session.exec(statement)
        return result.first()

    async def register_skill(
        self,
        name: str,
        description: str,
        category: str,
        instructions: str,
        tools_required: list[str] | None = None,
        is_system: bool = True,
    ) -> Skill:
        existing = await self.get_skill_by_name(name)
        if existing:
            existing.description = description
            existing.category = category
            existing.instructions = instructions
            existing.tools_required = tools_required or []
            self.session.add(existing)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing

        skill = Skill(
            name=name,
            description=description,
            category=category,
            instructions=instructions,
            tools_required=tools_required or [],
            is_system=is_system,
        )
        self.session.add(skill)
        await self.session.commit()
        await self.session.refresh(skill)
        return skill
