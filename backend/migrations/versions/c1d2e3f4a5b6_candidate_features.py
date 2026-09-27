"""add bandit, prompt-optimization, and red-team tables

Revision ID: c1d2e3f4a5b6
Revises: a1b2c3d4e5f6
Create Date: 2026-09-27 14:00:00.000000

Adds the three persistence tables behind the candidate feature sweep
(CRAG / structured output / prompt-opt / confidence / slices / bandit /
fairness / audit):

  * ``bandit_rewards``           — ε-greedy reward stream (experiment, variant,
                                   reward) read/written by the bandit service
  * ``prompt_optimization_runs`` — evidence trail of each automated prompt
                                   optimization loop (candidates, scores, winner)
  * ``redteam_runs``             — persisted red-team probe battery so the
                                   audit surface can show defense-rate history

Safety notes:
  * Every op is guarded (CREATE ... IF NOT EXISTS) so the migration also
    succeeds on databases where the dev ``create_all`` convenience already
    created these tables.
  * Foreign keys match the existing NO-ACTION convention (no cascades).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── bandit_rewards ────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bandit_rewards (
            id UUID PRIMARY KEY,
            experiment_key VARCHAR NOT NULL,
            variant VARCHAR NOT NULL,
            reward DOUBLE PRECISION NOT NULL DEFAULT 1.0,
            user_id UUID REFERENCES users(id),
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bandit_rewards_experiment_key ON bandit_rewards (experiment_key)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bandit_rewards_variant ON bandit_rewards (variant)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bandit_rewards_user_id ON bandit_rewards (user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bandit_rewards_created_at ON bandit_rewards (created_at)"
    )

    # ── prompt_optimization_runs ──────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_optimization_runs (
            id UUID PRIMARY KEY,
            prompt_key VARCHAR NOT NULL,
            baseline_prompt TEXT NOT NULL DEFAULT '',
            status VARCHAR NOT NULL DEFAULT 'running',
            candidate_count INTEGER NOT NULL DEFAULT 0,
            accepted_variant TEXT,
            baseline_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            best_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            average_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_prompt_optimization_runs_prompt_key ON prompt_optimization_runs (prompt_key)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_prompt_optimization_runs_created_at ON prompt_optimization_runs (created_at)"
    )

    # ── redteam_runs ──────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS redteam_runs (
            id UUID PRIMARY KEY,
            total_probes INTEGER NOT NULL DEFAULT 0,
            blocked_probes INTEGER NOT NULL DEFAULT 0,
            defense_rate DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            report JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_redteam_runs_created_at ON redteam_runs (created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS redteam_runs")
    op.execute("DROP TABLE IF EXISTS prompt_optimization_runs")
    op.execute("DROP TABLE IF EXISTS bandit_rewards")
