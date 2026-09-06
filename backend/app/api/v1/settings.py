"""
User Settings and BYOK API Keys API Router.
Pure HTTP transport layer — delegates to UserSettingsService (SRP + DIP).
"""
from uuid import UUID

from backend.app.api.deps import get_current_user, get_user_settings_service
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.user.models import User
from backend.app.domain.user.schemas import (
    APIKeyCreate,
    APIKeyResponse,
    UserMemoryCreate,
    UserMemoryResponse,
    UserSettingsResponse,
    UserSettingsUpdate,
)
from backend.app.services.user_settings_service import UserSettingsService
from fastapi import APIRouter, Depends, HTTPException, status

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=UserSettingsResponse)
async def get_settings(
    current_user: User = Depends(get_current_user),
    settings_svc: UserSettingsService = Depends(get_user_settings_service),
):
    return await settings_svc.get_settings(current_user.id)


@router.put("", response_model=UserSettingsResponse)
async def update_settings(
    settings_in: UserSettingsUpdate,
    current_user: User = Depends(get_current_user),
    settings_svc: UserSettingsService = Depends(get_user_settings_service),
):
    return await settings_svc.update_settings(current_user.id, settings_in)


@router.get("/memories", response_model=list[UserMemoryResponse])
async def list_memories(
    current_user: User = Depends(get_current_user),
    settings_svc: UserSettingsService = Depends(get_user_settings_service),
):
    return await settings_svc.list_memories(current_user.id)


@router.post("/memories", response_model=UserMemoryResponse, status_code=status.HTTP_201_CREATED)
async def add_memory(
    mem_in: UserMemoryCreate,
    current_user: User = Depends(get_current_user),
    settings_svc: UserSettingsService = Depends(get_user_settings_service),
):
    return await settings_svc.add_memory(current_user.id, mem_in)


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: UUID,
    current_user: User = Depends(get_current_user),
    settings_svc: UserSettingsService = Depends(get_user_settings_service),
):
    try:
        await settings_svc.delete_memory(current_user.id, memory_id)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)


@router.get("/keys", response_model=list[APIKeyResponse])
async def list_api_keys(
    current_user: User = Depends(get_current_user),
    settings_svc: UserSettingsService = Depends(get_user_settings_service),
):
    return await settings_svc.list_api_keys(current_user.id)


@router.post("/keys", response_model=APIKeyResponse, status_code=status.HTTP_201_CREATED)
async def save_api_key(
    key_in: APIKeyCreate,
    current_user: User = Depends(get_current_user),
    settings_svc: UserSettingsService = Depends(get_user_settings_service),
):
    return await settings_svc.save_api_key(
        current_user.id, key_in.provider, key_in.key_value, key_in.label
    )
