"""
E2B Code Interpreter Service.
Executes Python code in an isolated microVM sandbox with support for
data science packages, stdout/stderr streaming, and Matplotlib chart extraction.
"""
from typing import Any

import structlog
from backend.app.core.config import settings

logger = structlog.get_logger(__name__)


class CodeExecutionResult:
    def __init__(
        self,
        stdout: str = "",
        stderr: str = "",
        error: str | None = None,
        charts: list[dict[str, Any]] | None = None,
        execution_time_ms: float = 0.0,
    ):
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.charts = charts or []
        self.execution_time_ms = execution_time_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "charts": self.charts,
            "execution_time_ms": self.execution_time_ms,
            "success": self.error is None,
        }


class CodeExecutionService:
    """
    Executes Python scripts safely inside E2B Cloud Sandboxes.
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.E2B_API_KEY

    async def execute_python(
        self,
        code: str,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        """
        Runs Python code within the E2B Code Interpreter sandbox.
        Captures console outputs and chart visualizations.
        """
        import time
        start_time = time.time()

        if not self.api_key or self.api_key.startswith("e2b_placeholder"):
            logger.warning("e2b_api_key_not_configured_simulating_output")
            # Safe mock execution response when key is not yet set
            return CodeExecutionResult(
                stdout="[E2B Sandbox Notice: E2B_API_KEY not configured. Simulated execution output.]\nCode length: " + str(len(code)),
                execution_time_ms=(time.time() - start_time) * 1000,
            ).to_dict()

        try:
            from e2b_code_interpreter import AsyncSandbox

            stdout_lines: list[str] = []
            stderr_lines: list[str] = []
            charts: list[dict[str, Any]] = []

            async with AsyncSandbox(api_key=self.api_key, timeout=timeout_seconds) as sandbox:
                execution = await sandbox.run_code(code)

                for log in execution.logs.stdout:
                    stdout_lines.append(log)
                for log in execution.logs.stderr:
                    stderr_lines.append(log)

                # Process results and charts
                for res in execution.results:
                    if hasattr(res, "chart") and res.chart:
                        charts.append({
                            "type": "chart",
                            "data": res.chart.to_dict() if hasattr(res.chart, "to_dict") else str(res.chart),
                        })
                    elif hasattr(res, "png") and res.png:
                        charts.append({
                            "type": "image/png",
                            "data_base64": res.png,
                        })
                    elif hasattr(res, "text") and res.text:
                        stdout_lines.append(res.text)

                error_msg = None
                if execution.error:
                    error_msg = f"{execution.error.name}: {execution.error.value}\n{execution.error.traceback}"

                duration_ms = (time.time() - start_time) * 1000
                return CodeExecutionResult(
                    stdout="\n".join(stdout_lines),
                    stderr="\n".join(stderr_lines),
                    error=error_msg,
                    charts=charts,
                    execution_time_ms=duration_ms,
                ).to_dict()

        except Exception as exc:
            duration_ms = (time.time() - start_time) * 1000
            logger.error("e2b_execution_error", error=str(exc))
            return CodeExecutionResult(
                error=f"Sandbox execution failed: {str(exc)}",
                execution_time_ms=duration_ms,
            ).to_dict()


code_executor = CodeExecutionService()
