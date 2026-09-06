"""
Alembic environment configuration for Nexus AI Assistant database migrations.
Uses async engine for SQLModel + asyncpg compatibility.
"""
import asyncio
from logging.config import fileConfig
from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

# Import all models to ensure they are registered with SQLModel metadata
from backend.app.domain.user.models import User, UserSettings, UserMemory, APIKey
from backend.app.domain.conversation.models import Conversation, Message, MessageAttachment, ConversationBranch
from backend.app.domain.file.models import File, FileChunk, FileMetadata
from backend.app.domain.tool.models import Tool, ToolCall, ToolPermission
from backend.app.domain.prompt.models import PromptTemplate, PromptVersion, Skill
from backend.app.domain.usage.models import UsageLog, CostLog, EvaluationLog
from backend.app.domain.system.models import SystemConfig, AuditLog

# this is the Alembic Config object
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def get_database_url() -> str:
    import os
    from dotenv import load_dotenv
    load_dotenv()
    return os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://nexus:nexus@localhost:5432/nexus_dev"
    )


def run_migrations_offline() -> None:
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(get_database_url(), poolclass=pool.NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
