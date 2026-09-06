"""
Pydantic Schemas for User Domain (Pydantic V2 Standard).
"""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8)
    full_name: str | None = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenRefresh(BaseModel):
    refresh_token: str


class UserUpdate(BaseModel):
    full_name: str | None = None
    avatar_url: str | None = None
    password: str | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    username: str
    full_name: str | None = None
    avatar_url: str | None = None
    role: str
    is_active: bool
    is_verified: bool
    created_at: datetime
    updated_at: datetime


class UserSettingsUpdate(BaseModel):
    theme: str | None = "dark"
    default_model: str | None = "llama-3.3-70b-versatile"
    system_prompt_override: str | None = None
    temperature: float | None = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=4096, ge=1, le=32768)
    stream_response: bool | None = True
    enable_memory: bool | None = True
    enable_tools: bool | None = True
    custom_settings: dict[str, Any] | None = None


class UserSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    theme: str
    default_model: str
    system_prompt_override: str | None
    temperature: float
    max_tokens: int
    stream_response: bool
    enable_memory: bool
    enable_tools: bool
    custom_settings: dict[str, Any]


class UserMemoryCreate(BaseModel):
    content: str = Field(min_length=1)
    category: str = "preference"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_conversation_id: UUID | None = None


class UserMemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    content: str
    category: str
    confidence: float
    source_conversation_id: UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class APIKeyCreate(BaseModel):
    provider: str
    key_value: str
    label: str | None = None


class APIKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    provider: str
    key_preview: str
    label: str | None
    is_active: bool
    created_at: datetime
