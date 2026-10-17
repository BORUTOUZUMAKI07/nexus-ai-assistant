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

    # `execute_python` is the name the gateway is actually dispatched, and it is
    # `automatic` -- the sandbox is the isolation boundary. Approval is the
    # *conditional* rung: `requires_approval_if` in permissions.yaml. Before this
    # was evaluated the tool was absent from the YAML, fell through
    # `check_permission` to False, and the ladder could only ever say "no" or
    # "always" -- which is why no approval card was ever reachable.
    assert tool_gateway.check_permission("execute_python") is True
    benign = tool_gateway.requires_approval("execute_python", {"code": "print(sum(range(10)))"})
    assert benign == (False, None)

    flagged = tool_gateway.requires_approval("execute_python", {"code": "import os; os.system('ls -la')"})
    assert flagged[0] is True
    assert "system" in (flagged[1] or "")

    networked = tool_gateway.requires_approval(
        "execute_python", {"code": "import requests; requests.get('https://example.com')"}
    )
    assert networked[0] is True
    assert "network" in (networked[1] or "")
