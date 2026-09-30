import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Float, Index, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Column, Field, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "users"
    # The partial unique index is declared here (not only in the migration) so
    # Alembic's autogenerate sees it as part of the intended schema. Without
    # this, autogenerate treats any index it cannot find in the metadata as
    # extraneous and emits a DROP for it — quietly un-enforcing the one-IdP-
    # account-per-user rule.
    __table_args__ = (
        Index(
            "uq_users_oauth_identity",
            "oauth_provider",
            "oauth_sub",
            unique=True,
            postgresql_where=text(
                "oauth_provider IS NOT NULL AND oauth_sub IS NOT NULL"
            ),
        ),
        {"extend_existing": True},
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    email: str = Field(unique=True, index=True, nullable=False)
    username: str = Field(unique=True, index=True, nullable=False)
    hashed_password: str | None = Field(default=None)
    full_name: str | None = Field(default=None)
    avatar_url: str | None = Field(default=None)
    is_active: bool = Field(default=True)
    is_verified: bool = Field(default=False)
    role: str = Field(default="user", description="user | admin | moderator")
    # SSO linkage. ``oauth_sub`` is the provider's stable per-account identifier
    # (OIDC ``sub``) and is the ONLY trustworthy join key: an email claim can be
    # reassigned or recycled by the provider, a subject id cannot. The pair
    # (provider, sub) is unique so one IdP account maps to exactly one user.
    oauth_provider: str | None = Field(default=None, index=True)
    oauth_sub: str | None = Field(default=None, index=True)
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
    # Lifecycle. `confidence` used to be a hardcoded 1.0 that was never written
    # again, so every memory was permanently maximally trusted. It is now the
    # *observed* confidence: seeded low on write, raised on recall, decayed
    # toward CONFIDENCE_FLOOR when unused (see services/memory_lifecycle.py).
    confidence: float = Field(default=0.7)
    # mem0's id for the same fact, so the semantic index and this table can be
    # reconciled. Null for memories that only ever existed here.
    mem0_id: str | None = Field(default=None, index=True)
    # How many times this memory was retrieved and injected. Feeds the
    # "memories that prove useful survive" half of the lifecycle.
    retrieval_count: int = Field(default=0)
    # Last time it was retrieved (None = never used), and when it will have
    # decayed to the floor so a reaper can retire it.
    last_used_at: datetime | None = Field(default=None)
    # "user" for a standing preference, "conversation" for context that should
    # not outlive its thread.
    scope: str = Field(default="user", index=True)
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
