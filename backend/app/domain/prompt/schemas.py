"""
Pydantic Schemas for Prompt & Skill Domain (Pydantic V2 Standard).
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PromptTemplateCreate(BaseModel):
    title: str = Field(min_length=2, max_length=150)
    category: str = "general"
    system_prompt: str = Field(min_length=5)
    user_prompt_template: str | None = None
    input_variables: list[str] = Field(default_factory=list)
    is_public: bool = False


class PromptTemplateUpdate(BaseModel):
    title: str | None = None
    category: str | None = None
    system_prompt: str | None = None
    user_prompt_template: str | None = None
    input_variables: list[str] | None = None
    is_public: bool | None = None


class PromptTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None
    title: str
    category: str
    system_prompt: str
    user_prompt_template: str | None
    input_variables: list[str]
    is_public: bool
    version: int
    created_at: datetime
    updated_at: datetime


class SkillCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str
    category: str = "general"
    instructions: str = Field(min_length=10)
    tools_required: list[str] = Field(default_factory=list)
    is_system: bool = False


class SkillResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str
    category: str
    instructions: str
    tools_required: list[str]
    is_system: bool
    is_enabled: bool
    created_at: datetime
