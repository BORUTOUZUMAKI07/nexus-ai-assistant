# ruff: noqa: F401, I001
"""initial_schema

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-09-12 15:26:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlmodel import SQLModel

# Ensure all domain models are imported so their tables are bound to metadata
from backend.app.domain.user.models import User, UserSettings, UserMemory, APIKey
from backend.app.domain.conversation.models import Conversation, Message, MessageAttachment, ConversationBranch
from backend.app.domain.file.models import File, FileChunk, FileMetadata
from backend.app.domain.tool.models import Tool, ToolCall, ToolPermission
from backend.app.domain.prompt.models import PromptTemplate, PromptVersion, Skill
from backend.app.domain.usage.models import UsageLog, CostLog, EvaluationLog
from backend.app.domain.system.models import SystemConfig, AuditLog

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    SQLModel.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    SQLModel.metadata.drop_all(bind=bind)
