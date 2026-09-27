"""add org_id to usage and cost telemetry

Revision ID: d3e4f5a6b7c8
Revises: c1d2e3f4a5b6
Create Date: 2026-09-27 11:30:00.000000

Multi-tenant isolation (T-07): attributes each ``UsageLog`` and ``CostLog``
row to the caller's organization so per-org usage/cost rollups can be served
independently per tenant.

Both columns are NULLABLE so existing rows (written before org attribution
existed) remain valid, and foreign keys reference ``organizations`` with the
NO-ACTION convention used elsewhere (org deletion is explicitly cascaded by
AccountService/OrganizationService, never by the database).

Safety notes:
  * Every DDL is guarded (IF NOT EXISTS / IF EXISTS) so the migration also
    succeeds on databases where the dev ``create_all`` convenience already
    created these columns.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE usage_logs
            ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id)
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_usage_logs_org_id ON usage_logs (org_id)"
    )
    op.execute(
        """
        ALTER TABLE cost_logs
            ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id)
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cost_logs_org_id ON cost_logs (org_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_usage_logs_org_id")
    op.execute("ALTER TABLE usage_logs DROP COLUMN IF EXISTS org_id")
    op.execute("DROP INDEX IF EXISTS ix_cost_logs_org_id")
    op.execute("ALTER TABLE cost_logs DROP COLUMN IF EXISTS org_id")
