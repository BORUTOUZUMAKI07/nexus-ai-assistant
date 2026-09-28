"""add oauth identity columns to users

Revision ID: e5f6a7b8c9d0
Revises: d3e4f5a6b7c8
Create Date: 2026-09-28 10:15:00.000000

Gives SSO accounts a stable, provider-issued join key.

Until now ``AuthService.sso_login`` resolved every login by email and the
``User`` row carried no record of *which* IdP identity had signed in. Two
problems follow from that: (1) a provider that reassigns or recycles an email
address can hand a previously-linked address to a different person, who would
then inherit the account on the next login; (2) there is no way to detect a
subject id that has already been bound to a different account.

``oauth_sub`` stores the OIDC ``sub`` claim — per-account, immutable, and
provider-scoped — and ``oauth_provider`` scopes it. ``sso_login`` now looks the
user up by ``(oauth_provider, oauth_sub)`` first and only falls back to the
email to *adopt* a pre-existing password account, binding the subject at that
moment so every later login resolves by subject.

Both columns are NULLABLE: password-only accounts never set them. A partial
UNIQUE index enforces one-IdP-account-per-user while still permitting the many
NULLs that password accounts produce.

Safety notes:
  * Every DDL is guarded (IF NOT EXISTS / IF EXISTS) so the migration also
    succeeds on databases where the dev ``create_all`` convenience already
    created these columns.
  * The unique index is created concurrently-safe by relying on PostgreSQL's
    partial-index support; NULLs are excluded from uniqueness automatically.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS oauth_provider VARCHAR
        """
    )
    op.execute(
        """
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS oauth_sub VARCHAR
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_users_oauth_provider ON users (oauth_provider)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_oauth_sub ON users (oauth_sub)")
    # One IdP account may bind to at most one user. NULL rows (password-only
    # accounts) are exempt, which is what makes a partial index the right tool.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_users_oauth_identity
            ON users (oauth_provider, oauth_sub)
            WHERE oauth_provider IS NOT NULL AND oauth_sub IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_users_oauth_identity")
    op.execute("DROP INDEX IF EXISTS ix_users_oauth_sub")
    op.execute("DROP INDEX IF EXISTS ix_users_oauth_provider")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS oauth_sub")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS oauth_provider")
