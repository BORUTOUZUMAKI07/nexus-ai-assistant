"""
Domain models for Bandit Exploration rewards (MD §8.9 → industry ε-greedy).

``BanditReward`` is the persisted reward stream that backs the ε-greedy /
contextual bandit selection: every thumbs-up/thumbs-down on a message is
mapped to a (experiment_key, variant, reward) row so empirical win rates can be
recomputed over a rolling window without importing a bandit dependency.
"""
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class BanditReward(SQLModel, table=True):
    __tablename__ = "bandit_rewards"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    experiment_key: str = Field(index=True)  # e.g. chat_system_prompt
    variant: str = Field(index=True)         # e.g. canary_v1 / control
    reward: float = Field(default=1.0)       # 1.0 = thumbs_up, 0.0 = thumbs_down
    user_id: UUID | None = Field(default=None, index=True, foreign_key="users.id")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None), index=True
    )
