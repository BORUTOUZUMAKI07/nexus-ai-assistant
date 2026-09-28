# ruff: noqa: F401, I001, E402
"""
Alembic environment configuration for Nexus AI Assistant database migrations.
Uses async engine for SQLModel + asyncpg compatibility.
"""
import asyncio
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

# Shared pgbouncer-safe asyncpg settings: the hosted Postgres (Supabase)
# transaction-mode pooler conflicts with asyncpg's prepared-statement cache
# (DuplicatePreparedStatementError). The application engine uses the same
# options — keep these in sync with backend/app/infrastructure/database/engine.py.
PGBOUNCER_SAFE_CONNECT_ARGS = {
    "statement_cache_size": 0,
    "max_cached_statement_lifetime": 0,
}

# Import every model module so ALL tables are registered in SQLModel metadata
# before autogenerate compares it against the live database.
#
# This list must stay complete. ``target_metadata`` is the ONLY thing
# autogenerate compares against the database, so a model module missing here is
# a table it cannot see — and an unseen table is indistinguishable from one that
# should not exist, so the next ``alembic revision --autogenerate`` emits a DROP
# for it. That silently targets live, populated tables.
#
# When you add a domain, add its ``models`` module to this list in the same
# commit. The check: this list should yield the same table count as the
# database, except LangGraph's checkpoint_* tables, which are runtime-owned and
# filtered out by include_object below.
from backend.app.domain.user.models import User, UserSettings, UserMemory, APIKey
from backend.app.domain.conversation.models import Conversation, Message, MessageAttachment, ConversationBranch
from backend.app.domain.experiment.models import BanditReward
from backend.app.domain.file.models import File, FileChunk, FileMetadata
from backend.app.domain.tool.models import Tool, ToolCall, ToolPermission
from backend.app.domain.prompt.models import PromptTemplate, PromptVersion, Skill
from backend.app.domain.usage.models import UsageLog, CostLog, EvaluationLog
from backend.app.domain.system.models import SystemConfig, AuditLog
from backend.app.domain.plan.models import Plan
from backend.app.domain.hook.models import HookPolicy
from backend.app.domain.optimization.models import PromptOptimizationRun
from backend.app.domain.redteam.models import RedTeamRun
from backend.app.domain.artifact.models import Artifact, ArtifactVersion
from backend.app.domain.webhook.models import WebhookEndpoint, WebhookDelivery
from backend.app.domain.org.models import Organization, OrganizationMember, OrganizationInvite
from backend.app.domain.share.models import ConversationShare

# this is the Alembic Config object
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

# LangGraph checkpointer tables are created at runtime by
# langgraph-checkpoint-postgres and are intentionally NOT part of SQLModel
# metadata. Exclude them (and anything else owned by the graph runtime) from
# autogenerate comparisons so a future `revision --autogenerate` never proposes
# dropping them. See migrations/versions/9290fa24428d_codify_schema_drift.py.
#
# "checkpoints" is listed WITHOUT a trailing underscore deliberately: the main
# checkpointer table is named `checkpoints`, while its siblings are
# `checkpoint_blobs` / `checkpoint_writes` / `checkpoint_migrations`. A prefix
# of "checkpoint_" alone matches the siblings but NOT the main table, which is
# exactly the kind of near-miss that drops live conversation state.
RUNTIME_OWNED_TABLE_PREFIXES = ("checkpoints", "checkpoint_", "langgraph_", "sqlite_")


def include_object(
    object,
    name,
    type_,
    reflected,
    compare_to,
):
    if type_ == "table":
        return not any(name.startswith(p) for p in RUNTIME_OWNED_TABLE_PREFIXES)
    if type_ == "index" and name is not None:
        # Indexes on excluded runtime tables surface as reflected standalone
        # index objects — drop them from the comparison too.
        return not any(name.startswith("ix_" + p) for p in RUNTIME_OWNED_TABLE_PREFIXES)
    return True


def get_database_url() -> str:
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
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(
        get_database_url(),
        poolclass=pool.NullPool,
        connect_args=PGBOUNCER_SAFE_CONNECT_ARGS,
    )
    async with engine.begin() as conn:
        await conn.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
