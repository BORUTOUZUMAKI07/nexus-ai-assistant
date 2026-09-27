"""
Domain model for persisted Red-Team probe runs.

The red-team battery ran in-memory before; ``RedTeamRun`` makes the latest run
visible to the compliance/audit surface (defense rate, probe verdicts, and the
full per-probe report as JSONB) so block-rate trends can be tracked over time.
"""
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field, SQLModel


class RedTeamRun(SQLModel, table=True):
    __tablename__ = "redteam_runs"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    total_probes: int = Field(default=0)
    blocked_probes: int = Field(default=0)
    defense_rate: float = Field(default=0.0)  # 0..1
    report: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None), index=True
    )
