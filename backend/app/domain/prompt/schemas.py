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


class PromptTemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=2, max_length=150)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    system_prompt: str | None = Field(default=None, min_length=5, max_length=20000)
    user_prompt_template: str | None = Field(default=None, max_length=20000)
    input_variables: list[str] | None = Field(default=None, max_length=100)
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
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="general", min_length=1, max_length=64)
    instructions: str = Field(min_length=10, max_length=30000)
    tools_required: list[str] = Field(default_factory=list, max_length=50)
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
