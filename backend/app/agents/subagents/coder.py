"""
Coder Subagent with Automated Self-Correction Loop.
Generates code, executes it in the E2B sandbox, inspects errors,
and iteratively fixes bugs before returning results.
"""
import re
from typing import Any
from uuid import UUID

import structlog
from backend.app.core.config import settings
from backend.app.infrastructure.ai.litellm_client import ai_client
from backend.app.services.tools.tool_gateway import tool_gateway

logger = structlog.get_logger(__name__)

CODER_SYSTEM_PROMPT = """You are the Nexus Code Specialist - a production software engineer.
You write correct, idiomatic, well-typed Python and explain your reasoning only when it helps.

Rules:
1. Output complete, runnable code. No placeholders, TODO stubs, or pseudo-code unless the task is explicitly about design.
2. When a code answer is requested, return it as a single ```python ... ``` block and nothing else, so it can be executed directly.
3. Prefer the standard library and widely-supported packages. If a dependency is required, state it explicitly.
4. When given an execution error, read the traceback precisely: identify the root cause, fix the bug, and do not rerun the identical snippet.
5. Handle edge cases: empty inputs, boundary values, and obvious failure modes. Keep the code simple enough that it clearly works.
6. Never invent APIs, and never report test output you did not observe."""


class CoderSubagent:
    """
    Subagent that writes, tests, and auto-corrects code using E2B sandbox.
    """

    def extract_code(self, response: str) -> str:
        matches = re.findall(r"```(?:python)?\s*\n(.*?)\n```", response, re.DOTALL)
        if matches:
            return matches[0].strip()
        return response.strip()

    async def execute(self, task_description: str, max_retries: int = 2) -> dict[str, Any]:
        logger.info("coder_subagent_starting", task=task_description)

        messages = [
            {"role": "system", "content": CODER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Task: {task_description}\nWrite Python code to solve this task."},
        ]

        attempt = 0
        latest_code = ""
        latest_result = {}

        while attempt <= max_retries:
            # 1. Generate code
            response = await ai_client.completion(
                messages=messages,
                model=settings.FAST_MODEL,
                temperature=0.1,
                max_tokens=1024,
            )

            code = self.extract_code(response)
            latest_code = code

            # 2. Permission gate. A call the ladder flags (system commands,
            # network access) must not run on the model's say-so. Return it to
            # the graph instead: ``subagent_dispatcher_node`` queues it and
            # ``tool_node`` raises the HITL interrupt, so a human decides before
            # the sandbox ever sees it. Benign code runs below so the
            # self-correction loop keeps working.
            needs_approval, approval_reason = tool_gateway.requires_approval(
                "execute_python", {"code": code}
            )
            if needs_approval:
                logger.info("coder_code_requires_hitl_approval", attempt=attempt)
                return {
                    "subagent": "coder",
                    "status": "requires_approval",
                    "code": code,
                    "approval_reason": approval_reason,
                    "attempts": attempt + 1,
                }

            # 3. Execute in sandbox -- routed through the shared ``tool_gateway``
            # so the poka-yoke banned-pattern screen, lifecycle hooks and rate
            # limit apply to model-generated code too. Calling ``code_executor``
            # directly was an unpoliced second route to arbitrary code execution.
            # ``requires_approval`` above already returned the flagged cases, so
            # this proceeds only for code the ladder permits automatically.
            gw_result = await tool_gateway.execute_tool(
                tool_name="execute_python",
                arguments={"code": code},
                user_id=UUID(int=0),
                is_user_approved=False,
            )
            if gw_result.get("status") == "success":
                execution_res = gw_result.get("result") or {}
            else:
                execution_res = {
                    "success": False,
                    "error": gw_result.get("error")
                    or gw_result.get("message")
                    or gw_result.get("status", "execution failed"),
                }
            latest_result = execution_res

            # Check if succeeded
            if execution_res.get("success"):
                logger.info("coder_execution_succeeded", attempt=attempt)
                return {
                    "subagent": "coder",
                    "status": "success",
                    "code": code,
                    "stdout": execution_res.get("stdout", ""),
                    "charts": execution_res.get("charts", []),
                    "attempts": attempt + 1,
                }

            # If error, re-prompt for fix
            attempt += 1
            if attempt <= max_retries:
                error_msg = execution_res.get("error", "Unknown error")
                logger.warning("coder_execution_failed_retrying", attempt=attempt, error=error_msg)
                messages.append({"role": "assistant", "content": f"```python\n{code}\n```"})
                messages.append({
                    "role": "user",
                    "content": f"Execution failed with the following error:\n{error_msg}\n\nPlease analyze this error, fix the bug, and provide corrected Python code in a ```python block.",
                })

        return {
            "subagent": "coder",
            "status": "failed",
            "code": latest_code,
            "error": latest_result.get("error", "Max retries reached with errors"),
            "attempts": attempt,
        }


coder_subagent = CoderSubagent()
