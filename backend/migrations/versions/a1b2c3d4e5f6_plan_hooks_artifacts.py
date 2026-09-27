"""add plan, hook, and artifact tables

Revision ID: a1b2c3d4e5f6
Revises: 9290fa24428d
Create Date: 2026-09-27 10:00:00.000000

Adds the three feature tables from the "Plan mode + lifecycle hooks +
persisted artifacts" milestone:

  * ``plans``            — plan-then-approve contracts (approved → agent run)
  * ``hook_policies``    — lifecycle policy: block / redact / log at the tool
                           gateway choke point (global + org-scoped)
  * ``artifacts``        — persisted AI-generated files (current content)
  * ``artifact_versions``— immutable snapshots of every prior artifact revision

Safety notes:
  * Every op is guarded (CREATE ... IF NOT EXISTS) so the migration also
    succeeds on databases where the dev ``create_all`` convenience already
    created these tables.
  * Foreign keys match the existing NO-ACTION convention (no cascades)
    because conversations/users reference them and cascade cleanup is handled
    explicitly in ConversationRepository.delete / AccountService.delete_account.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "9290fa24428d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── plans ────────────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS plans (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES conversations(id),
            user_id UUID NOT NULL REFERENCES users(id),
            title VARCHAR NOT NULL,
            summary TEXT,
            steps JSONB NOT NULL DEFAULT '[]'::jsonb,
            status VARCHAR NOT NULL DEFAULT 'pending',
            decision_reason TEXT,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            decided_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_plans_conversation_id ON plans (conversation_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_plans_user_id ON plans (user_id)")

    # ── hook_policies ────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hook_policies (
            id UUID PRIMARY KEY,
            name VARCHAR NOT NULL,
            tool_name VARCHAR NOT NULL,
            event VARCHAR NOT NULL DEFAULT 'pre_tool',
            org_id UUID,
            action VARCHAR NOT NULL DEFAULT 'log',
            field VARCHAR,
            message VARCHAR(500),
            enabled BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_hook_policies_name ON hook_policies (name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_hook_policies_tool_name ON hook_policies (tool_name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_hook_policies_event ON hook_policies (event)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_hook_policies_org_id ON hook_policies (org_id)")

    # ── artifacts + artifact_versions ────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id),
            conversation_id UUID REFERENCES conversations(id),
            message_id UUID REFERENCES messages(id),
            title VARCHAR NOT NULL,
            language VARCHAR NOT NULL DEFAULT 'markdown',
            mime_type VARCHAR NOT NULL DEFAULT 'text/plain',
            content TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_artifacts_user_id ON artifacts (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_artifacts_conversation_id ON artifacts (conversation_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_artifacts_message_id ON artifacts (message_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS artifact_versions (
            id UUID PRIMARY KEY,
            artifact_id UUID NOT NULL REFERENCES artifacts(id),
            version INTEGER NOT NULL,
            title VARCHAR NOT NULL,
            language VARCHAR NOT NULL DEFAULT 'markdown',
            mime_type VARCHAR NOT NULL DEFAULT 'text/plain',
            content TEXT NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_artifact_versions_artifact_id ON artifact_versions (artifact_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS artifact_versions")
    op.execute("DROP TABLE IF EXISTS artifacts")
    op.execute("DROP TABLE IF EXISTS hook_policies")
    op.execute("DROP TABLE IF EXISTS plans")