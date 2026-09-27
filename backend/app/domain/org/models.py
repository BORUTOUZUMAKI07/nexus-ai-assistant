"""Domain models for organizations / workspaces (multi-tenant layer)."""
import uuid
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class Organization(SQLModel, table=True):
    __tablename__ = "organizations"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    name: str = Field(nullable=False)
    slug: str = Field(unique=True, index=True, nullable=False)
    description: str | None = Field(default=None)
    owner_id: uuid.UUID = Field(foreign_key="users.id", index=True, nullable=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class OrganizationMember(SQLModel, table=True):
    __tablename__ = "organization_members"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    organization_id: uuid.UUID = Field(foreign_key="organizations.id", index=True, nullable=False)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True, nullable=False)
    role: str = Field(default="member", description="owner | admin | member")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class OrganizationInvite(SQLModel, table=True):
    __tablename__ = "organization_invites"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    organization_id: uuid.UUID = Field(foreign_key="organizations.id", index=True, nullable=False)
    invited_by: uuid.UUID = Field(foreign_key="users.id", nullable=False)
    email: str = Field(index=True, nullable=False, description="Invitee email (match on accept)")
    role: str = Field(default="member", description="admin | member")
    token: str = Field(unique=True, index=True, nullable=False)
    status: str = Field(default="pending", description="pending | accepted | revoked")
    expires_at: datetime = Field(nullable=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))