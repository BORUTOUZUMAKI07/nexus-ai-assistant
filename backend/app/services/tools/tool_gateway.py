"""
Tool Gateway Service.
Enforces the 5-step validation ladder:
1. Schema Validation (Pydantic / jsonschema)
2. Poka-Yoke Security & Banned Patterns
3. Permission Ladder (Automatic vs Approval-Required)
4. Rate Limiting (Redis Sliding-Window)
5. Execution with Sandbox Isolation, Timeouts & Audit Telemetry
"""
import re
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
import yaml
from backend.app.core.exceptions import ToolExecutionError
from backend.app.infrastructure.cache.redis_client import redis_service
from backend.app.services.observability.metrics import metrics_collector
from backend.app.services.observability.tracing import trace_span
from backend.app.services.tools.code_execution import code_executor
from backend.app.services.tools.hook_registry import hook_registry
from backend.app.services.tools.web_search import web_search_service

logger = structlog.get_logger(__name__)

# Banned patterns for Poka-Yoke protection
BANNED_PATTERNS = [
    r"rm\s+-rf",
    r":\(\)\s*\{\s*:\|:\&\s*\};:",  # Fork bomb
    r"mkfs",
    r"dd\s+if=/dev/zero",
    r"curl\s+.*\s*\|\s*(ba)?sh",
    r"wget\s+.*\s*\|\s*(ba)?sh",
    r"(nc|netcat)\s+-e",
    r"/etc/shadow",
    r"/etc/passwd",
    r"\.env",
]

# Conditions named by ``requires_approval_if`` in config/permissions.yaml.
#
# The permission ladder has two *modes* -- a tool is either ``automatic`` or
# ``approval_required`` -- plus a conditional third rung: an automatic tool
# (the sandboxed code interpreter) that still needs a human when the specific
# call is high-impact. That rung was declared in the YAML and never evaluated,
# so model-written code reaching the gateway ran anything it liked with no
# approval and no card. These patterns are what make it real. They match
# *effects* (spawning processes, touching the filesystem, opening the network),
# not the incidental presence of the word "os".
_SYSTEM_COMMAND_PATTERNS = (
    r"\bos\.system\b",
    r"\bos\.popen\b",
    r"\bos\.spawn",
    r"\bos\.exec",
    r"\bos\.remove\b",
    r"\bos\.unlink\b",
    r"\bos\.rmdir\b",
    r"\bos\.rename\b",
    r"\bsubprocess\b",
    r"\bshutil\.rmtree\b",
    r"\bpty\.spawn",
    r"\bshell\s*=\s*True\b",
    r"\b(chmod|chown|chgrp|kill|pkill|killall)\b",
    r"\b(mkfs|fdisk|mount|umount)\b",
    r"\brm\s+-rf?\b",
)
_NETWORK_PATTERNS = (
    r"\bsocket\b",
    r"\burllib\b",
    r"\brequests\b",
    r"\bhttpx\b",
    r"\baiohttp\b",
    r"\bhttp\.client\b",
    r"\bsmtplib\b",
    r"\bftplib\b",
    r"\btelnetlib\b",
    r"\bparamiko\b",
    r"\basyncio\.open_connection\b",
    r"\bcurl\b",
    r"\bwget\b",
    r"\bwebbrowser\b",
)
# Human-readable form of each condition, shown on the approval card. A missing
# entry falls back to the raw condition name rather than to nothing, so a new
# YAML condition is legible before anyone remembers to add a sentence here.
_APPROVAL_CONDITION_REASONS = {
    "contains_system_commands": "it runs shell or system commands",
    "contains_socket_calls": "it opens a network connection",
    "execution_time_exceeds_20s": "it is declared to run longer than 20 seconds",
}


class ToolGateway:
    """
    Central gateway through which all LLM tool calls are vetted, verified, and executed.
    """

    def __init__(self, permissions_config_path: str | None = None):
        self.permissions_config_path = permissions_config_path or str(
            Path(__file__).resolve().parent.parent.parent.parent / "config" / "permissions.yaml"
        )
        self.permissions_config = self._load_permissions()

    def _load_permissions(self) -> dict[str, Any]:
        try:
            with open(self.permissions_config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning("failed_to_load_permissions_yaml_using_defaults", error_type=type(exc).__name__)
            return {
                "automatic": ["web_search", "web_scrape", "calculator"],
                "approval_required": ["execute_python", "file_write", "terminal_command"],
            }

    def validate_poka_yoke(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """
        Step 2: Check input arguments against banned security patterns.
        """
        arg_str = str(arguments)
        for pattern in BANNED_PATTERNS:
            if re.search(pattern, arg_str, re.IGNORECASE):
                logger.error("poka_yoke_violation", tool_name=tool_name, pattern=pattern)
                raise ToolExecutionError(
                    f"Poka-Yoke Security Violation: Arguments match prohibited pattern '{pattern}'"
                )

    def check_permission(self, tool_name: str) -> bool:
        """
        Step 3: Checks whether tool is automatic (True) or requires approval (False).
        """
        tools_dict = self.permissions_config.get("tools", {})
        if tool_name in tools_dict:
            mode = tools_dict[tool_name].get("mode", "approval_required")
            return mode == "automatic"

        auto_tools = self.permissions_config.get("automatic", [])
        return tool_name in auto_tools

    def _approval_conditions(self, tool_name: str) -> list[str]:
        """The ``requires_approval_if`` conditions declared for this tool."""
        entry = self.permissions_config.get("tools", {}).get(tool_name, {})
        conditions = entry.get("requires_approval_if") or []
        return conditions if isinstance(conditions, list) else []

    @staticmethod
    def _argument_text(arguments: dict[str, Any]) -> str:
        """Flatten argument values to one string for the effect-pattern scan."""
        return " ".join(str(v) for v in arguments.values())

    def _condition_matches(
        self, condition: str, arguments: dict[str, Any], code: str
    ) -> bool:
        """Evaluate one ``requires_approval_if`` condition against a concrete call."""
        if condition == "contains_system_commands":
            return any(re.search(p, code, re.IGNORECASE) for p in _SYSTEM_COMMAND_PATTERNS)
        if condition == "contains_socket_calls":
            return any(re.search(p, code, re.IGNORECASE) for p in _NETWORK_PATTERNS)
        if condition == "execution_time_exceeds_20s":
            raw = arguments.get("timeout_seconds", arguments.get("timeout"))
            try:
                return int(raw) > 20
            except (TypeError, ValueError):
                return False
        return False

    def requires_approval(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> tuple[bool, str | None]:
        """The full permission-ladder decision for one concrete call.

        Returns ``(needs_approval, reason)``. ``check_permission`` only answers
        the *mode* question (automatic vs. approval-required); this adds the
        ``requires_approval_if`` conditions, so a normally-automatic tool whose
        arguments are high-impact still asks.

        This is the single source both ``tool_node`` (which raises the HITL card)
        and ``execute_tool`` (which must refuse an unapproved call) consult, so
        the card and the gate can never disagree about whether a call needed a
        human.
        """
        if not self.check_permission(tool_name):
            return True, f"'{tool_name}' is on the approval-required list."
        code = self._argument_text(arguments)
        for condition in self._approval_conditions(tool_name):
            if self._condition_matches(condition, arguments, code):
                reason = _APPROVAL_CONDITION_REASONS.get(condition, condition)
                return True, f"'{tool_name}' requires approval because {reason}."
        return False, None

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_id: UUID,
        is_user_approved: bool = False,
        org_id: UUID | None = None,
    ) -> dict[str, Any]:
        """
        Executes the tool call following the 5-step safety ladder.

        ``org_id`` is the caller's resolved organization (if any) used to scope
        lifecycle hook policies; None means only global (org_id=NULL) policies
        apply.
        """
        start_time = time.time()
        logger.info("tool_execution_requested", tool_name=tool_name, user_id=str(user_id))

        # 1. Poka-yoke validation
        self.validate_poka_yoke(tool_name, arguments)

        # 2. Permission Ladder Check (mode + requires_approval_if conditions)
        needs_approval, approval_reason = self.requires_approval(tool_name, arguments)
        if needs_approval and not is_user_approved:
            logger.info("tool_requires_approval", tool_name=tool_name, reason=approval_reason)
            return {
                "status": "requires_approval",
                "tool_name": tool_name,
                "arguments": arguments,
                "message": (
                    f"Execution of tool '{tool_name}' requires explicit user confirmation."
                    + (f" {approval_reason}" if approval_reason else "")
                ),
            }

        # 2.5 Lifecycle hooks (pre-tool policy: block / redact / log).
        pre_verdict = hook_registry.evaluate_pre(tool_name, arguments, org_id=org_id)
        if pre_verdict.blocked:
            logger.warning("tool_blocked_by_hook", tool_name=tool_name, message=pre_verdict.message)
            return {
                "status": "blocked",
                "tool_name": tool_name,
                "message": pre_verdict.message
                or f"Execution of tool '{tool_name}' is blocked by policy.",
            }
        if pre_verdict.redacted is not None:
            arguments = pre_verdict.redacted
        for entry in pre_verdict.logs:
            logger.info("tool_hook_log", tool_name=tool_name, **entry)

        # 3. Rate Limit Check (20 tool calls per minute per user)
        # Fail-open when Redis is unavailable: rate limiting degrades gracefully
        # instead of crashing the request, since Redis is not core to execution.
        rate_key = f"tool:{user_id}"
        try:
            allowed, _ = await redis_service.check_rate_limit(rate_key, limit=20, window_seconds=60)
        except Exception as exc:
            logger.warning("redis_rate_limit_skipped_redis_unavailable", error_type=type(exc).__name__)
            allowed = True
        if not allowed:
            raise ToolExecutionError("Tool execution rate limit exceeded. Please wait a minute.")

        # 4. Dispatch to actual tool implementation
        try:
            # Tool arguments can contain credentials, prompts, or user content.
            # Keep traces to non-sensitive dimensions only.
            async with trace_span(
                f"tool_execute_{tool_name}",
                {"tool_name": tool_name, "user_id": str(user_id)},
            ):
                result = await self._dispatch(tool_name, arguments)
            # 4.5 Lifecycle hooks (post-tool policy: block / redact / log).
            post_verdict = hook_registry.evaluate_post(tool_name, result, org_id=org_id)
            if post_verdict.blocked:
                logger.warning("tool_result_blocked_by_hook", tool_name=tool_name, message=post_verdict.message)
                return {
                    "status": "blocked",
                    "tool_name": tool_name,
                    "message": post_verdict.message
                    or f"Result of tool '{tool_name}' is blocked by policy.",
                }
            if post_verdict.redacted is not None:
                result = post_verdict.redacted
            for entry in post_verdict.logs:
                logger.info("tool_hook_log", tool_name=tool_name, **entry)
            duration_ms = (time.time() - start_time) * 1000
            metrics_collector.increment(f"tool_calls:{tool_name}")
            metrics_collector.record_latency(f"tool:{tool_name}", duration_ms)
            return {
                "status": "success",
                "tool_name": tool_name,
                "result": result,
                "duration_ms": duration_ms,
            }
        except Exception as exc:
            duration_ms = (time.time() - start_time) * 1000
            metrics_collector.record_error(f"tool_{tool_name}_{type(exc).__name__}")
            logger.error("tool_dispatch_failed", tool_name=tool_name, error_type=type(exc).__name__, error=str(exc))
            return {
                "status": "error",
                "tool_name": tool_name,
                "error": str(exc),
                "duration_ms": duration_ms,
            }

    async def _dispatch(self, tool_name: str, args: dict[str, Any]) -> Any:
        """
        Internal dispatcher to registered tools.
        """
        if tool_name in ("execute_python", "execute_python_code"):
            code = args.get("code", "")
            timeout = args.get("timeout_seconds", 30)
            return await code_executor.execute_python(code=code, timeout_seconds=timeout)

        elif tool_name == "web_search":
            query = args.get("query", "")
            max_results = args.get("max_results", 5)
            return await web_search_service.search(query=query, max_results=max_results)

        elif tool_name == "web_scrape":
            url = args.get("url", "")
            return await web_search_service.scrape_url(url=url)

        elif tool_name in ("calculator", "calculate_expression"):
            # Restricted AST interpreter — NEVER eval() (sandbox escape vector).
            # Mirrors mcp/server._safe_math_eval; lazy import avoids cycles.
            from backend.app.mcp.server import _safe_math_eval
            expression = args.get("expression", "")
            result = _safe_math_eval(expression)
            return {"expression": expression, "result": result}

        else:
            raise ToolExecutionError(f"Unregistered tool: '{tool_name}'")


tool_gateway = ToolGateway()
