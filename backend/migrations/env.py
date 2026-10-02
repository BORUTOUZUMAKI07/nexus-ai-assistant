# ruff: noqa: F401, I001, E402
"""
Alembic environment configuration for Nexus AI Assistant database migrations.
Uses async engine for SQLModel + asyncpg compatibility.
"""
import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from backend.app.core.db_target import (
    ALLOW_REMOTE_MIGRATIONS_ENV,
    describe_database_target,
    migration_target_refusal,
    remote_migrations_allowed,
)

# Commands that change a live schema, and therefore the only ones gated.
# Read-only commands (`history`, `current`, `heads`, `revision --autogenerate`)
# are deliberately NOT gated: inspecting a remote database is legitimate and
# gating it would only teach people to reach for the opt-in.
MUTATING_COMMANDS = frozenset({"upgrade", "downgrade"})


def _requested_command() -> str | None:
    """The Alembic subcommand from argv, or None if it is not a bare verb.

    `context.config.cmd_opts` would be the typed route, but its shape varies by
    Alembic version and this file must keep working across upgrades. argv is the
    dispatch the CLI actually used: `alembic upgrade head` puts the verb at
    index 1. Only an exact token counts, so `-x upgrade=1` and a revision message
    reading "upgrade the table" cannot trip the gate.
    """
    for arg in sys.argv[1:]:
        if arg in MUTATING_COMMANDS:
            return arg
    return None


def assert_migration_target_allowed(url: str) -> None:
    """Refuse to mutate a remote schema without an explicit opt-in.

    This is the guard for the 2026-10-01 incident, in the one place that
    actually caused it. `alembic upgrade head` read DATABASE_URL out of
    `backend/.env`, which carried a hosted Supabase DSN, and applied a revision
    to the live database. It turned out additive and idempotent, so nothing was
    lost -- but that was a property of the specific revision, not of the tool.
    The same command with a DROP would have been irreversible.

    The fix is not "be careful". It is that a schema-changing command states
    which database it is about to change and refuses when the answer is not the
    one the operator typed, because the DSN is not something the operator typed.

    Offline mode (`--sql`) is exempt: it prints statements to stdout and never
    opens a connection, so there is nothing to protect. Gating it would block
    the standard way to *preview* a migration, and blocking the preview is how
    people end up running the real thing without reading it.

    The mode is read from `context.is_offline_mode()` and takes no override
    parameter. An earlier version took `offline: bool | None = None` "for
    testability", and the revert harness proved the override was worse than
    useless: every test passed the flag explicitly, so the line that reads the
    real Alembic state ran in production and never in a test -- deleting it
    entirely left the suite green. A second source of truth for a value the
    framework already knows is a liability even when it is only there for tests.
    """
    if context.is_offline_mode():
        return

    command = _requested_command()
    if command is None:
        return

    target = describe_database_target(url)
    if target.is_local:
        return

    if remote_migrations_allowed():
        opt_in = os.environ.get(ALLOW_REMOTE_MIGRATIONS_ENV, "")
        sys.stderr.write(
            f"WARNING: `alembic {command}` is migrating the REMOTE database "
            f"{target.describe()} because {ALLOW_REMOTE_MIGRATIONS_ENV}={opt_in!r} "
            f"is set. You asked for this.\n\n"
        )
        return

    raise RuntimeError(migration_target_refusal(target, command))

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
    assert_migration_target_allowed(url)
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
    url = get_database_url()
    assert_migration_target_allowed(url)
    engine = create_async_engine(
        url,
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
