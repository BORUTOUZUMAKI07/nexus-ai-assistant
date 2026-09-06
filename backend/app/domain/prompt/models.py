import uuid
from datetime import UTC, datetime

from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field, SQLModel


class PromptTemplate(SQLModel, table=True):
    __tablename__ = "prompt_templates"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True, nullable=False)
    title: str = Field(nullable=False)
    category: str = Field(default="general", index=True)
    system_prompt: str = Field(nullable=False)
    user_prompt_template: str | None = Field(default=None, sa_type=Text)
    input_variables: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    version: int = Field(default=1)
    is_public: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class PromptVersion(SQLModel, table=True):
    __tablename__ = "prompt_versions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    template_id: uuid.UUID = Field(foreign_key="prompt_templates.id", index=True, nullable=False)
    version: int = Field(nullable=False)
    system_prompt: str = Field(nullable=False)
    user_prompt_template: str | None = Field(default=None, sa_type=Text)
    created_by: uuid.UUID = Field(foreign_key="users.id", nullable=False)
    change_summary: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class Skill(SQLModel, table=True):
    __tablename__ = "skills"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(unique=True, index=True, nullable=False)
    description: str = Field(nullable=False)
    category: str = Field(default="general", index=True)
    instructions: str = Field(nullable=False, description="Full markdown instructions")
    tools_required: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    is_system: bool = Field(default=False)
    is_enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
