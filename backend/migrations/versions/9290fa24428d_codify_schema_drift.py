"""codify schema drift

Revision ID: 9290fa24428d
Revises: 0001_initial_schema
Create Date: 2026-09-26 18:42:40.880419

Brings a database created by an early ``create_all`` up to the current
SQLModel schema WITHOUT touching the LangGraph checkpointer tables
(checkpoints / checkpoint_blobs / checkpoint_writes / checkpoint_migrations;
those are created at runtime by langgraph-checkpoint-postgres and are not part
of SQLModel metadata).

Safety notes:
  * Every op is idempotent or guarded (IF EXISTS / IF NOT EXISTS), so the
    migration also succeeds on a FRESH database produced by 0001's create_all
    (where most of this drift is already present).
  * Removed columns (files.size_bytes, tool_calls.result,
    user_settings.system_prompt) are dropped only IF EXISTS — they are legacy
    leftovers the application no longer reads or writes.
  * NOT NULL alters are preceded by DEFAULT backfills for any legacy NULL rows.
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '9290fa24428d'
down_revision: Union[str, None] = '0001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ─────────────────────────────────────────────────────────────────────────────
# Upgrade
# ─────────────────────────────────────────────────────────────────────────────

def _backfill() -> None:
    """Bring legacy NULL values to their declared defaults before tightening."""
    stmts = [
        "UPDATE files SET status = 'pending' WHERE status IS NULL",
        "UPDATE files SET chunk_count = 0 WHERE chunk_count IS NULL",
        "UPDATE files SET original_filename = filename WHERE original_filename IS NULL",
        "UPDATE files SET updated_at = created_at WHERE updated_at IS NULL",
        "UPDATE conversations SET is_pinned = false WHERE is_pinned IS NULL",
        "UPDATE conversations SET token_count = 0 WHERE token_count IS NULL",
        "UPDATE file_chunks SET is_parent = false WHERE is_parent IS NULL",
        "UPDATE message_attachments SET file_size_bytes = 0 WHERE file_size_bytes IS NULL",
        "UPDATE messages SET prompt_tokens = 0 WHERE prompt_tokens IS NULL",
        "UPDATE messages SET completion_tokens = 0 WHERE completion_tokens IS NULL",
        "UPDATE messages SET total_tokens = 0 WHERE total_tokens IS NULL",
        "UPDATE prompt_templates SET category = 'general' WHERE category IS NULL",
        "UPDATE prompt_templates SET title = '' WHERE title IS NULL",
        "UPDATE prompt_templates SET system_prompt = '' WHERE system_prompt IS NULL",
        "UPDATE prompt_templates SET input_variables = '[]'::jsonb WHERE input_variables IS NULL",
        "UPDATE prompt_versions SET version = 1 WHERE version IS NULL",
        "UPDATE prompt_versions SET system_prompt = '' WHERE system_prompt IS NULL",
        "UPDATE skills SET category = 'general' WHERE category IS NULL",
        "UPDATE skills SET instructions = '' WHERE instructions IS NULL",
        "UPDATE skills SET tools_required = '[]'::jsonb WHERE tools_required IS NULL",
        "UPDATE skills SET is_system = false WHERE is_system IS NULL",
        "UPDATE tool_calls SET input_args = '{}'::jsonb WHERE input_args IS NULL",
        "UPDATE tool_calls SET execution_time_ms = 0 WHERE execution_time_ms IS NULL",
        "UPDATE tool_calls SET requires_approval = false WHERE requires_approval IS NULL",
        "UPDATE tools SET category = 'general' WHERE category IS NULL",
        "UPDATE tools SET parameters_schema = '{}'::jsonb WHERE parameters_schema IS NULL",
        "UPDATE tools SET timeout_seconds = 30 WHERE timeout_seconds IS NULL",
        "UPDATE tools SET is_system = false WHERE is_system IS NULL",
        "UPDATE tools SET updated_at = now() WHERE updated_at IS NULL",
        "UPDATE user_settings SET stream_response = true WHERE stream_response IS NULL",
        "UPDATE user_settings SET enable_memory = true WHERE enable_memory IS NULL",
        "UPDATE user_settings SET enable_tools = true WHERE enable_tools IS NULL",
    ]
    for sql in stmts:
        op.execute(sa.text(sql))


def _drop_legacy_columns() -> None:
    """Remove orphaned legacy columns (guarded — never fails if already gone)."""
    op.execute("ALTER TABLE files DROP COLUMN IF EXISTS size_bytes")
    op.execute("ALTER TABLE tool_calls DROP COLUMN IF EXISTS result")
    op.execute("ALTER TABLE user_settings DROP COLUMN IF EXISTS system_prompt")


def _create_fk(table: str, column: str, ref: str, ref_col: str) -> None:
    constraint = f"{table}_{column}_fkey"
    # Constraint names are compile-time constants below, so they are safely
    # interpolated — asyncpg/PG do not accept bound parameters inside DO blocks.
    # FKs are added NOT VALID: legacy databases may contain historical orphan
    # rows (e.g. tool_calls whose conversation was deleted), and NOT VALID
    # enforces the constraint on all NEW writes while skipping the potentially
    # dirty existing rows. Fresh installs already have a VALID constraint under
    # this exact name, so the guard is a no-op there.
    op.execute(
        sa.text(
            f"DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{constraint}') THEN "
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
            f"FOREIGN KEY ({column}) REFERENCES {ref} ({ref_col}) NOT VALID; "
            f"END IF; END $$;"
        )
    )


def _create_unique(table: str, column: str, name: str) -> None:
    op.execute(
        sa.text(
            f"DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN "
            f"ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE ({column}); "
            f"END IF; END $$;"
        )
    )


def _create_index(name: str, table: str, column: str) -> None:
    op.execute(sa.text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})"))


def upgrade() -> None:
    _backfill()

    # ── conversation_branches ─────────────────────────────────────────────────
    op.alter_column('conversation_branches', 'conversation_id',
                    existing_type=sa.UUID(), nullable=False)
    op.alter_column('conversation_branches', 'fork_message_id',
                    existing_type=sa.UUID(), nullable=False)
    _create_unique('conversation_branches', 'conversation_id',
                   'conversation_branches_conversation_id_key')
    _create_fk('conversation_branches', 'conversation_id', 'conversations', 'id')
    _create_fk('conversation_branches', 'fork_message_id', 'messages', 'id')

    # ── conversations ─────────────────────────────────────────────────────────
    op.alter_column('conversations', 'is_pinned', existing_type=sa.BOOLEAN(),
                    nullable=False, existing_server_default=sa.text('false'))
    op.alter_column('conversations', 'token_count', existing_type=sa.INTEGER(),
                    nullable=False, existing_server_default=sa.text('0'))

    # ── file_chunks ───────────────────────────────────────────────────────────
    op.alter_column('file_chunks', 'is_parent', existing_type=sa.BOOLEAN(),
                    nullable=False, existing_server_default=sa.text('false'))
    _create_index('ix_file_chunks_parent_chunk_id', 'file_chunks', 'parent_chunk_id')
    _create_fk('file_chunks', 'parent_chunk_id', 'file_chunks', 'id')

    # ── files ─────────────────────────────────────────────────────────────────
    op.alter_column('files', 'original_filename', existing_type=sa.VARCHAR(), nullable=False)
    op.alter_column('files', 'status', existing_type=sa.VARCHAR(), nullable=False,
                    existing_server_default=sa.text("'pending'::character varying"))
    op.alter_column('files', 'chunk_count', existing_type=sa.INTEGER(), nullable=False,
                    existing_server_default=sa.text('0'))
    op.alter_column('files', 'updated_at', existing_type=postgresql.TIMESTAMP(), nullable=False)
    _create_index('ix_files_conversation_id', 'files', 'conversation_id')
    _create_fk('files', 'conversation_id', 'conversations', 'id')

    # ── message_attachments / messages ────────────────────────────────────────
    op.alter_column('message_attachments', 'filename', existing_type=sa.VARCHAR(), nullable=False)
    op.alter_column('message_attachments', 'file_type', existing_type=sa.VARCHAR(), nullable=False)
    op.alter_column('message_attachments', 'file_size_bytes', existing_type=sa.INTEGER(),
                    nullable=False, existing_server_default=sa.text('0'))
    op.alter_column('messages', 'prompt_tokens', existing_type=sa.INTEGER(), nullable=False,
                    existing_server_default=sa.text('0'))
    op.alter_column('messages', 'completion_tokens', existing_type=sa.INTEGER(), nullable=False,
                    existing_server_default=sa.text('0'))
    op.alter_column('messages', 'total_tokens', existing_type=sa.INTEGER(), nullable=False,
                    existing_server_default=sa.text('0'))
    _create_fk('messages', 'parent_message_id', 'messages', 'id')

    # ── prompt_templates / prompt_versions / skills ───────────────────────────
    op.alter_column('prompt_templates', 'title', existing_type=sa.VARCHAR(), nullable=False)
    op.alter_column('prompt_templates', 'category', existing_type=sa.VARCHAR(), nullable=False,
                    existing_server_default=sa.text("'general'::character varying"))
    op.alter_column('prompt_templates', 'system_prompt', existing_type=sa.VARCHAR(), nullable=False)
    op.alter_column('prompt_templates', 'input_variables',
                    existing_type=postgresql.JSONB(astext_type=sa.Text()), nullable=False)
    _create_index('ix_prompt_templates_category', 'prompt_templates', 'category')
    op.alter_column('prompt_versions', 'version', existing_type=sa.INTEGER(), nullable=False)
    op.alter_column('prompt_versions', 'system_prompt', existing_type=sa.VARCHAR(), nullable=False)
    op.alter_column('prompt_versions', 'created_by', existing_type=sa.UUID(), nullable=False)
    _create_fk('prompt_versions', 'created_by', 'users', 'id')
    op.alter_column('skills', 'category', existing_type=sa.VARCHAR(), nullable=False,
                    existing_server_default=sa.text("'general'::character varying"))
    op.alter_column('skills', 'instructions', existing_type=sa.VARCHAR(), nullable=False)
    op.alter_column('skills', 'tools_required',
                    existing_type=postgresql.JSONB(astext_type=sa.Text()), nullable=False)
    op.alter_column('skills', 'is_system', existing_type=sa.BOOLEAN(), nullable=False,
                    existing_server_default=sa.text('false'))
    _create_index('ix_skills_category', 'skills', 'category')

    # ── tool_calls / tools ────────────────────────────────────────────────────
    op.alter_column('tool_calls', 'conversation_id', existing_type=sa.UUID(), nullable=False)
    op.alter_column('tool_calls', 'input_args',
                    existing_type=postgresql.JSONB(astext_type=sa.Text()), nullable=False)
    op.alter_column('tool_calls', 'execution_time_ms', existing_type=sa.INTEGER(),
                    type_=sa.Float(), nullable=False,
                    postgresql_using='execution_time_ms::double precision')
    op.alter_column('tool_calls', 'requires_approval', existing_type=sa.BOOLEAN(), nullable=False,
                    existing_server_default=sa.text('false'))
    _create_index('ix_tool_calls_conversation_id', 'tool_calls', 'conversation_id')
    _create_fk('tool_calls', 'conversation_id', 'conversations', 'id')
    op.alter_column('tools', 'category', existing_type=sa.VARCHAR(), nullable=False,
                    existing_server_default=sa.text("'general'::character varying"))
    op.alter_column('tools', 'parameters_schema',
                    existing_type=postgresql.JSONB(astext_type=sa.Text()), nullable=False)
    op.alter_column('tools', 'timeout_seconds', existing_type=sa.INTEGER(), nullable=False,
                    existing_server_default=sa.text('30'))
    op.alter_column('tools', 'is_system', existing_type=sa.BOOLEAN(), nullable=False,
                    existing_server_default=sa.text('false'))
    op.alter_column('tools', 'updated_at', existing_type=postgresql.TIMESTAMP(), nullable=False)
    _create_index('ix_tools_category', 'tools', 'category')

    # ── user_settings ─────────────────────────────────────────────────────────
    op.alter_column('user_settings', 'stream_response', existing_type=sa.BOOLEAN(),
                    nullable=False, existing_server_default=sa.text('true'))
    op.alter_column('user_settings', 'enable_memory', existing_type=sa.BOOLEAN(),
                    nullable=False, existing_server_default=sa.text('true'))
    op.alter_column('user_settings', 'enable_tools', existing_type=sa.BOOLEAN(),
                    nullable=False, existing_server_default=sa.text('true'))

    # ── legacy column cleanup (best effort, never fails on a fresh install) ───
    _drop_legacy_columns()


# ─────────────────────────────────────────────────────────────────────────────
# Downgrade
# ─────────────────────────────────────────────────────────────────────────────

def _drop_fk(table: str, column: str) -> None:
    constraint = f"{table}_{column}_fkey"
    op.execute(
        sa.text(
            f"DO $$ BEGIN "
            f"IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{constraint}') THEN "
            f"ALTER TABLE {table} DROP CONSTRAINT {constraint}; "
            f"END IF; END $$;"
        )
    )


def _drop_constraint(table: str, name: str) -> None:
    op.execute(
        sa.text(
            f"DO $$ BEGIN "
            f"IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN "
            f"ALTER TABLE {table} DROP CONSTRAINT {name}; "
            f"END IF; END $$;"
        )
    )


def _drop_index(name: str) -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {name}"))


def _restore_legacy_columns() -> None:
    op.execute(
        "ALTER TABLE files ADD COLUMN IF NOT EXISTS size_bytes INTEGER"
    )
    op.execute(
        "ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS result JSONB"
    )
    op.execute(
        "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS system_prompt TEXT"
    )


def downgrade() -> None:
    op.alter_column('user_settings', 'enable_tools', existing_type=sa.BOOLEAN(), nullable=True,
                    existing_server_default=sa.text('true'))
    op.alter_column('user_settings', 'enable_memory', existing_type=sa.BOOLEAN(), nullable=True,
                    existing_server_default=sa.text('true'))
    op.alter_column('user_settings', 'stream_response', existing_type=sa.BOOLEAN(), nullable=True,
                    existing_server_default=sa.text('true'))
    _drop_index('ix_tools_category')
    op.alter_column('tools', 'updated_at', existing_type=postgresql.TIMESTAMP(), nullable=True)
    op.alter_column('tools', 'is_system', existing_type=sa.BOOLEAN(), nullable=True,
                    existing_server_default=sa.text('false'))
    op.alter_column('tools', 'timeout_seconds', existing_type=sa.INTEGER(), nullable=True,
                    existing_server_default=sa.text('30'))
    op.alter_column('tools', 'parameters_schema',
                    existing_type=postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    op.alter_column('tools', 'category', existing_type=sa.VARCHAR(), nullable=True,
                    existing_server_default=sa.text("'general'::character varying"))
    _drop_index('ix_tool_calls_conversation_id')
    _drop_fk('tool_calls', 'conversation_id')
    op.alter_column('tool_calls', 'requires_approval', existing_type=sa.BOOLEAN(), nullable=True,
                    existing_server_default=sa.text('false'))
    op.alter_column('tool_calls', 'execution_time_ms', existing_type=sa.Float(),
                    type_=sa.INTEGER(), nullable=True,
                    postgresql_using='execution_time_ms::integer')
    op.alter_column('tool_calls', 'input_args',
                    existing_type=postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    op.alter_column('tool_calls', 'conversation_id', existing_type=sa.UUID(), nullable=True)
    _drop_index('ix_skills_category')
    op.alter_column('skills', 'is_system', existing_type=sa.BOOLEAN(), nullable=True,
                    existing_server_default=sa.text('false'))
    op.alter_column('skills', 'tools_required',
                    existing_type=postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    op.alter_column('skills', 'instructions', existing_type=sa.VARCHAR(), nullable=True)
    op.alter_column('skills', 'category', existing_type=sa.VARCHAR(), nullable=True,
                    existing_server_default=sa.text("'general'::character varying"))
    _drop_fk('prompt_versions', 'created_by')
    op.alter_column('prompt_versions', 'created_by', existing_type=sa.UUID(), nullable=True)
    op.alter_column('prompt_versions', 'system_prompt', existing_type=sa.VARCHAR(), nullable=True)
    op.alter_column('prompt_versions', 'version', existing_type=sa.INTEGER(), nullable=True)
    _drop_index('ix_prompt_templates_category')
    op.alter_column('prompt_templates', 'input_variables',
                    existing_type=postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    op.alter_column('prompt_templates', 'system_prompt', existing_type=sa.VARCHAR(), nullable=True)
    op.alter_column('prompt_templates', 'category', existing_type=sa.VARCHAR(), nullable=True,
                    existing_server_default=sa.text("'general'::character varying"))
    op.alter_column('prompt_templates', 'title', existing_type=sa.VARCHAR(), nullable=True)
    _drop_fk('messages', 'parent_message_id')
    op.alter_column('messages', 'total_tokens', existing_type=sa.INTEGER(), nullable=True,
                    existing_server_default=sa.text('0'))
    op.alter_column('messages', 'completion_tokens', existing_type=sa.INTEGER(), nullable=True,
                    existing_server_default=sa.text('0'))
    op.alter_column('messages', 'prompt_tokens', existing_type=sa.INTEGER(), nullable=True,
                    existing_server_default=sa.text('0'))
    op.alter_column('message_attachments', 'file_size_bytes', existing_type=sa.INTEGER(),
                    nullable=True, existing_server_default=sa.text('0'))
    op.alter_column('message_attachments', 'file_type', existing_type=sa.VARCHAR(), nullable=True)
    op.alter_column('message_attachments', 'filename', existing_type=sa.VARCHAR(), nullable=True)
    _drop_index('ix_files_conversation_id')
    _drop_fk('files', 'conversation_id')
    op.alter_column('files', 'updated_at', existing_type=postgresql.TIMESTAMP(), nullable=True)
    op.alter_column('files', 'chunk_count', existing_type=sa.INTEGER(), nullable=True,
                    existing_server_default=sa.text('0'))
    op.alter_column('files', 'status', existing_type=sa.VARCHAR(), nullable=True,
                    existing_server_default=sa.text("'pending'::character varying"))
    op.alter_column('files', 'original_filename', existing_type=sa.VARCHAR(), nullable=True)
    _drop_index('ix_file_chunks_parent_chunk_id')
    _drop_fk('file_chunks', 'parent_chunk_id')
    op.alter_column('file_chunks', 'is_parent', existing_type=sa.BOOLEAN(), nullable=True,
                    existing_server_default=sa.text('false'))
    op.alter_column('conversations', 'token_count', existing_type=sa.INTEGER(), nullable=True,
                    existing_server_default=sa.text('0'))
    op.alter_column('conversations', 'is_pinned', existing_type=sa.BOOLEAN(), nullable=True,
                    existing_server_default=sa.text('false'))
    _drop_constraint('conversation_branches', 'conversation_branches_conversation_id_key')
    _drop_fk('conversation_branches', 'fork_message_id')
    _drop_fk('conversation_branches', 'conversation_id')
    op.alter_column('conversation_branches', 'fork_message_id', existing_type=sa.UUID(), nullable=True)
    op.alter_column('conversation_branches', 'conversation_id', existing_type=sa.UUID(), nullable=True)

    # Note: LangGraph checkpointer tables are intentionally NOT recreated here —
    # they are owned by the runtime, not by SQLModel migrations.
    _restore_legacy_columns()