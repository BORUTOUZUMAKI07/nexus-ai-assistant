"""Projects router.

Creates and manages Projects — lightweight conversation groupings similar to
ChatGPT's "Projects". Owner-scoped; conversations can be moved between
projects via the existing PATCH /conversations/{id} endpoint (project_id).
"""
from uuid import UUID

from backend.app.api.deps import get_current_user, get_db
from backend.app.domain.conversation.schemas import ProjectCreate, ProjectResponse, ProjectUpdate
from backend.app.domain.user.models import User
from backend.app.services.project_service import ProjectService
from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

router = APIRouter(prefix="/projects", tags=["projects"])


def _svc(db: AsyncSession = Depends(get_db)) -> ProjectService:
    return ProjectService(db)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    user: User = Depends(get_current_user),
    svc: ProjectService = Depends(_svc),
):
    return await svc.list_projects(user.id)


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    data: ProjectCreate,
    user: User = Depends(get_current_user),
    svc: ProjectService = Depends(_svc),
):
    return await svc.create_project(user.id, data)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    data: ProjectUpdate,
    user: User = Depends(get_current_user),
    svc: ProjectService = Depends(_svc),
):
    return await svc.update_project(project_id, user.id, data)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: UUID,
    user: User = Depends(get_current_user),
    svc: ProjectService = Depends(_svc),
):
    await svc.delete_project(project_id, user.id)
