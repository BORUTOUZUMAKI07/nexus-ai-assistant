"""
Repository for Prompt and Skill domain operations.
"""
from datetime import UTC, datetime
from uuid import UUID

from backend.app.domain.base_repository import BaseRepository
from backend.app.domain.prompt.models import PromptTemplate, PromptVersion, Skill
from sqlalchemy import text
from sqlmodel import or_, select
from sqlmodel.ext.asyncio.session import AsyncSession


def _library_lock_key(user_id: UUID) -> int:
    """A stable 64-bit key for one user's prompt library.

    Advisory locks are keyed on an integer, so the uuid is folded into one. The
    collision space is 2**63 and the lock is held for milliseconds, so a shared
    key costs a brief wait between two unrelated users; it never corrupts
    anything.
    """
    return user_id.int % 9223372036854775807


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

        template.updated_at = datetime.now(UTC).replace(tzinfo=None)
        self.session.add(template)
        await self.session.commit()
        await self.session.refresh(template)
        return template

    async def lock_library_seeding(self, user_id: UUID) -> None:
        """Serialise starter-template seeding for one user.

        Checking "is this library empty?" and then inserting into it is a
        check-then-act sequence, and two requests that open the same fresh
        account at once both see an empty library and both insert the starters --
        the user ends up with duplicate templates that then multiply. A
        transaction-scoped advisory lock makes the pair atomic: the second request
        waits, then sees the rows the first one committed.

        Fail-open on non-PostgreSQL dialects (no advisory locks there) and on any
        error: this is a seeding optimisation, and refusing to serve the library
        because a lock could not be taken would be worse than the race.
        """
        try:
            bind = self.session.get_bind()
            dialect = getattr(getattr(bind, "dialect", None), "name", "")
        except Exception:
            return
        if dialect != "postgresql":
            return
        # `bindparams`, not `params=`: SQLModel's `exec` takes the value on the
        # statement, and passing a parameter dict here was silently dropped in a
        # concurrent run -- the lock was never taken, so two first loads still
        # raced and one of them failed its insert.
        await self.session.exec(
            text("SELECT pg_advisory_xact_lock(:key)").bindparams(
                key=_library_lock_key(user_id)
            )
        )

    async def create_templates_atomic(
        self, user_id: UUID, items: list[dict[str, object]]
    ) -> list[PromptTemplate]:
        """Insert a batch of templates and their first versions in ONE commit.

        One commit, not one per row: the advisory lock above is released by the
        commit, so a per-row commit would hand the next request a half-seeded
        library and it would seed the rest again as duplicates.
        """
        created: list[PromptTemplate] = []
        for item in items:
            template = PromptTemplate(
                user_id=user_id,
                title=str(item["title"]),
                category=str(item.get("category") or "general"),
                system_prompt=str(item["system_prompt"]),
                user_prompt_template=(
                    str(item["user_prompt_template"])
                    if item.get("user_prompt_template")
                    else None
                ),
                input_variables=list(item.get("input_variables") or []),  # type: ignore[arg-type]
                is_public=False,
                version=1,
            )
            self.session.add(template)
            created.append(template)

        await self.session.flush()
        for template in created:
            self.session.add(
                PromptVersion(
                    template_id=template.id,
                    version=1,
                    system_prompt=template.system_prompt,
                    user_prompt_template=template.user_prompt_template,
                    created_by=user_id,
                    change_summary="Initial creation",
                )
            )
        await self.session.commit()
        for template in created:
            await self.session.refresh(template)
        return created

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
