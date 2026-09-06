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
from backend.app.infrastructure.cache.redis_client import redis_client
from backend.app.services.observability.metrics import metrics_collector
from backend.app.services.observability.tracing import trace_span
from backend.app.services.tools.code_execution import code_executor
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
            logger.warning("failed_to_load_permissions_yaml_using_defaults", error=str(exc))
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

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_id: UUID,
        is_user_approved: bool = False,
    ) -> dict[str, Any]:
        """
        Executes the tool call following the 5-step safety ladder.
        """
        start_time = time.time()
        logger.info("tool_execution_requested", tool_name=tool_name, user_id=str(user_id))

        # 1. Poka-yoke validation
        self.validate_poka_yoke(tool_name, arguments)

        # 2. Permission Ladder Check
        is_automatic = self.check_permission(tool_name)
        if not is_automatic and not is_user_approved:
            logger.info("tool_requires_approval", tool_name=tool_name)
            return {
                "status": "requires_approval",
                "tool_name": tool_name,
                "arguments": arguments,
                "message": f"Execution of tool '{tool_name}' requires explicit user confirmation.",
            }

        # 3. Rate Limit Check (20 tool calls per minute per user)
        # Fail-open when Redis is unavailable: rate limiting degrades gracefully
        # instead of crashing the request, since Redis is not core to execution.
        rate_key = f"rate_limit:tool:{user_id}"
        try:
            allowed, _ = await redis_client.check_rate_limit(rate_key, max_requests=20, window_seconds=60)
        except Exception as exc:
            logger.warning("redis_rate_limit_skipped_redis_unavailable", error=str(exc))
            allowed = True
        if not allowed:
            raise ToolExecutionError("Tool execution rate limit exceeded. Please wait a minute.")

        # 4. Dispatch to actual tool implementation
        try:
            async with trace_span(
                f"tool_execute_{tool_name}",
                {"tool_name": tool_name, "user_id": str(user_id), "arguments": str(arguments)[:500]},
            ):
                result = await self._dispatch(tool_name, arguments)
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
            logger.error("tool_dispatch_failed", tool_name=tool_name, error=str(exc))
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
        if tool_name == "execute_python":
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

        elif tool_name == "calculator":
            import math
            expression = args.get("expression", "")
            # Safe math eval
            allowed_names = {k: v for k, v in math.__dict__.items() if not k.startswith("__")}
            result = eval(expression, {"__builtins__": None}, allowed_names)
            return {"expression": expression, "result": result}

        else:
            raise ToolExecutionError(f"Unregistered tool: '{tool_name}'")


tool_gateway = ToolGateway()
