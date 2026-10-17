"""add unique constraints on organization_members and api_keys

Revision ID: d4e5f6a7b8c9
Revises: c2a1b2c3d4e5
Create Date: 2026-10-13 00:00:00.000000

Two tables had no database-level uniqueness where the application assumed it.

``organization_members`` had one row per (organization, user) only by
construction: every membership insert went through a read-then-write that was
correct in a single process and racy under concurrency, so two simultaneous
invites could produce two membership rows and a user could appear twice in an
org. ``api_keys`` had the same shape per (user, provider) -- a retried create
could leave two active keys for one provider.

Both are fixed at the model layer (``UniqueConstraint`` in
``domain/org/models.py`` and ``domain/user/models.py``) and pinned here, because
``create_all`` at startup only builds tables that do not exist: on every
database that already has these tables the model change alone is invisible.

Safety notes:
  * Every op is guarded with a ``pg_constraint`` existence check, matching the
    ``IF NOT EXISTS`` convention of the other migrations in this directory, so a
    database created fresh by ``create_all`` (which already carries the
    constraints) upgrades cleanly.
  * Additive only: nothing is read, rewritten or dropped. If a database already
    contains duplicate rows the constraint is the point -- it fails loudly here
    rather than leaving the duplicates silently unconstrained.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c2a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Exact names declared in the SQLModel ``__table_args__``. Not optional: an
# unnamed constraint gets a database-generated name and autogenerate would then
# report the declared one as missing forever.
_ORG_MEMBER_UQ = "uq_org_member_org_user"
_API_KEY_UQ = "uq_api_key_user_provider"


def _add_unique_if_absent(constraint: str, table: str, columns: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{constraint}') THEN
                ALTER TABLE {table} ADD CONSTRAINT {constraint} UNIQUE ({columns});
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    _add_unique_if_absent(_ORG_MEMBER_UQ, "organization_members", "organization_id, user_id")
    _add_unique_if_absent(_API_KEY_UQ, "api_keys", "user_id, provider")


def downgrade() -> None:
    op.execute(f"ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS {_API_KEY_UQ}")
    op.execute(
        f"ALTER TABLE organization_members DROP CONSTRAINT IF EXISTS {_ORG_MEMBER_UQ}"
    )
