"""
Unit tests for Tool Gateway Poka-Yoke & Permission Ladder.
"""
import pytest
from backend.app.core.exceptions import ToolExecutionError
from backend.app.services.tools.tool_gateway import tool_gateway


def test_poka_yoke_banned_patterns():
    # Safe argument
    tool_gateway.validate_poka_yoke("execute_python", {"code": "print('Hello world!')"})

    # Prohibited shell command
    with pytest.raises(ToolExecutionError) as exc_info:
        tool_gateway.validate_poka_yoke("execute_python", {"code": "import os; os.system('rm -rf /')"})
    assert "Poka-Yoke Security Violation" in str(exc_info.value)


def test_permission_ladder():
    assert tool_gateway.check_permission("web_search") is True
    assert tool_gateway.check_permission("calculator") is True
    assert tool_gateway.check_permission("execute_python") is False  # requires approval
