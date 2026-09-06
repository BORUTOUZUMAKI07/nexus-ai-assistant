"""
Prompt Templates and Skills API Router.
Pure HTTP transport layer — delegates to PromptService (SRP + DIP).
"""

from backend.app.api.deps import get_current_user, get_prompt_service
from backend.app.domain.prompt.schemas import (
    PromptTemplateCreate,
    PromptTemplateResponse,
    SkillResponse,
)
from backend.app.domain.user.models import User
from backend.app.services.prompt_service import PromptService
from fastapi import APIRouter, Depends, status

router = APIRouter(prefix="/prompts", tags=["prompts"])


@router.get("/templates", response_model=list[PromptTemplateResponse])
async def list_templates(
    include_public: bool = True,
    current_user: User = Depends(get_current_user),
    prompt_svc: PromptService = Depends(get_prompt_service),
):
    return await prompt_svc.list_templates(
        user_id=current_user.id, include_public=include_public
    )


@router.post("/templates", response_model=PromptTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    template_in: PromptTemplateCreate,
    current_user: User = Depends(get_current_user),
    prompt_svc: PromptService = Depends(get_prompt_service),
):
    return await prompt_svc.create_template(
        user_id=current_user.id, template_in=template_in
    )


@router.get("/skills", response_model=list[SkillResponse])
async def list_skills(
    enabled_only: bool = True,
    current_user: User = Depends(get_current_user),
    prompt_svc: PromptService = Depends(get_prompt_service),
):
    return await prompt_svc.list_skills(enabled_only=enabled_only)
