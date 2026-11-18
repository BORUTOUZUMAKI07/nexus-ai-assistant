"""repair live database integrity defects

Revision ID: e7f8a9b0c1d2
Revises: z1a2b3c4d5e6
Create Date: 2026-11-18

A read-only audit of the hosted Supabase database found:

  * one ``tool_calls`` row whose NOT NULL ``conversation_id`` points at a
    conversation that no longer exists (the FK added in 9290fa24428d was
    NOT VALID, so the bad row was accepted historically);
  * seven legacy FK constraints still marked NOT VALID;
  * no database CHECK restricting ``organization_members.role`` to the four
    values the application can write.

Every other relationship was checked and had zero violations, all role values
currently stored are valid, and the three nil UUIDs present are LangGraph
``__resume__`` task sentinels, not application data.

This revision is intentionally narrow: it deletes only that one orphan row,
validates the seven already-existing constraints, and adds the role CHECK.
It does not attempt a general backfill or touch any other data.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, None] = 'z1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Remove the single orphan tool call. NOT NULL means it cannot be
    #    repaired by nulling the reference; the referenced conversation is gone.
    op.execute("""
        DELETE FROM tool_calls
        WHERE id = 'd93e2740-a0d2-4f1d-a2fc-f496a4953413'
          AND NOT EXISTS (
              SELECT 1 FROM conversations c WHERE c.id = tool_calls.conversation_id
          )
    """)

    # 2. Promote the seven legacy FKs from NOT VALID to validated. Existing
    #    rows were checked individually and are clean after the delete above.
    op.execute(
        "ALTER TABLE conversation_branches "
        "VALIDATE CONSTRAINT conversation_branches_conversation_id_fkey"
    )
    op.execute(
        "ALTER TABLE conversation_branches "
        "VALIDATE CONSTRAINT conversation_branches_fork_message_id_fkey"
    )
    op.execute(
        "ALTER TABLE file_chunks "
        "VALIDATE CONSTRAINT file_chunks_parent_chunk_id_fkey"
    )
    op.execute(
        "ALTER TABLE files VALIDATE CONSTRAINT files_conversation_id_fkey"
    )
    op.execute(
        "ALTER TABLE messages VALIDATE CONSTRAINT messages_parent_message_id_fkey"
    )
    op.execute(
        "ALTER TABLE prompt_versions "
        "VALIDATE CONSTRAINT prompt_versions_created_by_fkey"
    )
    op.execute(
        "ALTER TABLE tool_calls VALIDATE CONSTRAINT tool_calls_conversation_id_fkey"
    )

    # 3. Match the database to the role set OrganizationService can write.
    op.execute("""
        ALTER TABLE organization_members
          ADD CONSTRAINT ck_organization_members_role
          CHECK (role IN ('owner','admin','member','viewer'))
    """)


def downgrade() -> None:
    """No-op by design.

    The deleted row cannot be restored faithfully (its conversation is gone),
    and reverting validated FKs to NOT VALID would only re-hide integrity
    defects. Dropping the role CHECK is the only reversible part, but leaving
    it in place is strictly safer than removing a constraint that rejects no
    valid application data.
    """
    pass
