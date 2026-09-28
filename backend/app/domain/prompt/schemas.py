"""
Pydantic Schemas for Prompt & Skill Domain (Pydantic V2 Standard).
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PromptTemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=150)
    category: str = Field(default="general", min_length=1, max_length=64)
    system_prompt: str = Field(min_length=5, max_length=20000)
    user_prompt_template: str | None = Field(default=None, max_length=20000)
    input_variables: list[str] = Field(default_factory=list, max_length=100)
    is_public: bool = False


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
