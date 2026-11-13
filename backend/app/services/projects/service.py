"""
Project Application Service.

Groups conversations into projects (a lightweight organizational unit
similar to ChatGPT's "Projects"). Owner-scoped: a project's members
(conversations) are always filtered by user_id.
"""
from typing import Any
from uuid import UUID

import structlog
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.conversation.models import Project
from backend.app.domain.conversation.schemas import ProjectCreate, ProjectUpdate
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class ProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_projects(self, user_id: UUID) -> list[Project]:
        stmt = (
            select(Project)
            .where(Project.user_id == user_id)
            .order_by(Project.updated_at.desc())
        )
        result = await self._session.exec(stmt)
        return list(result.all())

    async def get_project(self, project_id: UUID, user_id: UUID) -> Project | None:
        stmt = select(Project).where(Project.id == project_id, Project.user_id == user_id)
        result = await self._session.exec(stmt)
        return result.first()

    async def create_project(self, user_id: UUID, data: ProjectCreate) -> Project:
        project = Project(user_id=user_id, name=data.name, description=data.description)
        self._session.add(project)
        await self._session.commit()
        await self._session.refresh(project)
        logger.info("project_created", project_id=str(project.id), user_id=str(user_id))
        return project

    async def update_project(
        self, project_id: UUID, user_id: UUID, data: ProjectUpdate
    ) -> Project:
        project = await self.get_project(project_id, user_id)
        if not project:
            raise ResourceNotFoundError("Project", str(project_id))
        if data.name is not None:
            project.name = data.name
        if data.description is not None:
            project.description = data.description
        await self._session.commit()
        await self._session.refresh(project)
        return project

    async def delete_project(self, project_id: UUID, user_id: UUID) -> None:
        project = await self.get_project(project_id, user_id)
        if not project:
            raise ResourceNotFoundError("Project", str(project_id))
        # Move conversations out so nothing is lost, then delete the project.
        from backend.app.domain.conversation.models import Conversation

        update_stmt = select(Conversation).where(
            Conversation.user_id == user_id, Conversation.project_id == project_id
        )
        result = await self._session.exec(update_stmt)
        convs = list(result.all())
        for conv in convs:
            conv.project_id = None
            self._session.add(conv)
        await self._session.commit()
        await self._session.delete(project)
        await self._session.commit()
        logger.info("project_deleted", project_id=str(project_id), moved=len(convs))

    def public_dict(self, project: Project) -> dict[str, Any]:
        return {
            "id": str(project.id),
            "name": project.name,
            "description": project.description,
            "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat(),
        }
