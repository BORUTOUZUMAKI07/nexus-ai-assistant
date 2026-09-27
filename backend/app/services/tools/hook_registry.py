"""
In-memory hook policy registry (DB-free evaluation on the tool hot path).

The gateway evaluates policies against a plain-dict snapshot loaded by
HookService (admin CRUD + app startup both call ``set_snapshot``), so no
database or ORM object is touched per tool call. Fail-open semantics: an empty
registry or a malformed policy simply evaluates to no-op.
"""
import threading
from dataclasses import dataclass, field
from typing import Any

import structlog
from backend.app.core.redaction import redact_value

logger = structlog.get_logger(__name__)


@dataclass
class HookVerdict:
    """Result of evaluating the pre/post lifecycle hooks for one tool call."""

    blocked: bool = False
    message: str | None = None
    redacted: dict[str, Any] | None = None
    logs: list[dict[str, Any]] = field(default_factory=list)


class HookRegistry:
    """Holds a read-only snapshot of enabled hook policies (id + plain dicts)."""

    def __init__(self) -> None:
        self._policies: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    # ── snapshot management ──────────────────────────────────────────────────

    def set_snapshot(self, policies: list[Any]) -> None:
        """Replace the policy snapshot (accepts model instances or dicts)."""
        plain: list[dict[str, Any]] = []
        for p in policies:
            if hasattr(p, "__table__"):  # SQLModel row
                plain.append({c.name: getattr(p, c.name) for c in p.__table__.columns})
            elif isinstance(p, dict):
                plain.append(dict(p))
        with self._lock:
            self._policies = plain
        logger.info("hook_registry_snapshot_loaded", count=len(plain))

    @property
    def policy_count(self) -> int:
        return len(self._policies)

    # ── evaluation ───────────────────────────────────────────────────────────

    def _matches(self, policy: dict[str, Any], tool_name: str, org_id: Any) -> bool:
        if not policy.get("enabled", True):
            return False
        target = policy.get("tool_name")
        if target not in ("*", tool_name):
            return False
        policy_org = policy.get("org_id")
        if policy_org is not None:
            if org_id is None or str(policy_org) != str(org_id):
                return False
        return True

    def evaluate_pre(self, tool_name: str, arguments: dict[str, Any], org_id: Any = None) -> HookVerdict:
        """Pre-tool policy: block / redact arguments / log before dispatch."""
        with self._lock:
            snapshot = list(self._policies)
        verdict = HookVerdict()
        working: dict[str, Any] = dict(arguments)
        for policy in snapshot:
            if policy.get("event") != "pre_tool" or not self._matches(policy, tool_name, org_id):
                continue
            action = policy.get("action", "log")
            name = policy.get("name") or "?"
            if action == "block":
                message = policy.get("message") or (
                    f"Tool '{tool_name}' is blocked by hook policy '{name}'."
                )
                return HookVerdict(blocked=True, message=message)
            if action == "redact":
                field = policy.get("field")
                if field:
                    if field in working:
                        working[field] = redact_value(working[field])
                else:
                    working = redact_value(working)
                verdict.redacted = working
                verdict.logs.append({"policy": name, "action": "redact", "field": field})
            else:
                verdict.logs.append({"policy": name, "action": "log"})
        return verdict

    def evaluate_post(self, tool_name: str, result: Any, org_id: Any = None) -> HookVerdict:
        """Post-tool policy: block / redact result / log after dispatch."""
        with self._lock:
            snapshot = list(self._policies)
        verdict = HookVerdict()
        working: Any = result
        for policy in snapshot:
            if policy.get("event") != "post_tool" or not self._matches(policy, tool_name, org_id):
                continue
            action = policy.get("action", "log")
            name = policy.get("name") or "?"
            if action == "block":
                message = policy.get("message") or (
                    f"Tool '{tool_name}' result blocked by hook policy '{name}'."
                )
                return HookVerdict(blocked=True, message=message)
            if action == "redact":
                field = policy.get("field")
                if field and isinstance(working, dict) and field in working:
                    working = dict(working)
                    working[field] = redact_value(working[field])
                else:
                    working = redact_value(working)
                verdict.redacted = working
                verdict.logs.append({"policy": name, "action": "redact", "field": field})
            else:
                verdict.logs.append({"policy": name, "action": "log"})
        return verdict


hook_registry = HookRegistry()
