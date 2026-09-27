"""
Hook Domain Model.

A ``HookPolicy`` is a lifecycle policy applied to tool calls at the gateway
choke point. Hooks evaluate in-memory (plain-dict snapshot — no DB on the hot
path) and support three actions:

  * ``block``  — refuse the tool call (403 via ToolPermissionError)
  * ``redact`` — scrub PII/secrets from the arguments (pre) or result (post)
  * ``log``    — record the call for audit/observability

``org_id`` is NULL for global (all users) or an organization id for
org-scoped policies. Matching org membership is resolved by the caller
(ToolService, which has the session); the registry itself only compares ids.
"""
import uuid
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class HookPolicy(SQLModel, table=True):
    __tablename__ = "hook_policies"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    name: str = Field(nullable=False, index=True)
    # "*" matches every tool; otherwise an exact tool name.
    tool_name: str = Field(nullable=False, index=True)
    # "pre_tool" (arguments before dispatch) | "post_tool" (result after dispatch).
    event: str = Field(default="pre_tool", index=True)
    # NULL = global policy; set = org-scoped policy.
    org_id: uuid.UUID | None = Field(default=None, index=True)
    # "block" | "redact" | "log"
    action: str = Field(default="log", nullable=False)
    # For "redact": which top-level key of the arguments/result to scrub.
    # When omitted/falsy, the entire payload is scrubbed.
    field: str | None = Field(default=None)
    # Custom block/log message surfaced to the caller/logs.
    message: str | None = Field(default=None)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
