import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Float, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Column, Field, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "users"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    email: str = Field(unique=True, index=True, nullable=False)
    username: str = Field(unique=True, index=True, nullable=False)
    hashed_password: str | None = Field(default=None)
    full_name: str | None = Field(default=None)
    avatar_url: str | None = Field(default=None)
    is_active: bool = Field(default=True)
    is_verified: bool = Field(default=False)
    role: str = Field(default="user", description="user | admin | moderator")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class UserSettings(SQLModel, table=True):
    __tablename__ = "user_settings"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", unique=True, index=True, nullable=False)
    default_model: str = Field(default="llama-3.3-70b-versatile")
    temperature: float = Field(default=0.7)
    max_tokens: int = Field(default=4096)
    system_prompt_override: str | None = Field(default=None, sa_type=Text)
    theme: str = Field(default="dark", description="dark | light | system")
    language: str = Field(default="en")
    totp_secret: str | None = Field(default=None)
    stream_response: bool = Field(default=True)
    enable_memory: bool = Field(default=True)
    enable_tools: bool = Field(default=True)
    custom_settings: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class UserMemory(SQLModel, table=True):
    __tablename__ = "user_memories"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True, nullable=False)
    category: str = Field(default="preference", description="preference | fact | skill | context | goal | role")
    content: str = Field(sa_type=Text)
    confidence: float = Field(default=1.0)
    source_conversation_id: uuid.UUID | None = Field(default=None)
    is_active: bool = Field(default=True)
    embedding: list[float] | None = Field(default=None, sa_column=Column(ARRAY(Float)))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class APIKey(SQLModel, table=True):
    __tablename__ = "api_keys"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True, nullable=False)
    provider: str = Field(description="openai | anthropic | groq | openrouter | gemini")
    encrypted_key: str = Field(nullable=False)
    key_preview: str = Field(nullable=False, description="Last 4 chars hint for UI display")
    label: str | None = Field(default=None)
    is_active: bool = Field(default=True)
    scopes: list[str] = Field(default=["chat", "tools"], sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
