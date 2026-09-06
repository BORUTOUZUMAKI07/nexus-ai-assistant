"""
Coder Subagent with Automated Self-Correction Loop.
Generates code, executes it in the E2B sandbox, inspects errors,
and iteratively fixes bugs before returning results.
"""
import re
from typing import Any

import structlog
from backend.app.infrastructure.ai.litellm_client import ai_client
from backend.app.services.tools.code_execution import code_executor

logger = structlog.get_logger(__name__)

CODER_SYSTEM_PROMPT = """You are the Nexus Code Specialist.
You write production-grade, bug-free Python code.
When instructed, output only executable Python code in ```python ... ``` blocks.
If provided an execution error, analyze the traceback and produce corrected code."""


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
                model="llama-3.3-70b-versatile",
                temperature=0.1,
            )

            code = self.extract_code(response)
            latest_code = code

            # 2. Execute in sandbox
            execution_res = await code_executor.execute_python(code=code)
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
