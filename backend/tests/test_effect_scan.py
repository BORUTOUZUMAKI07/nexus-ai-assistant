"""Strix finding #10: the approval gate must *resolve* code, not grep it.

The human-approval gate for ``execute_python`` used to decide whether a call was
high-impact by running regexes over the source string. Every evasion below runs a
real effect while spelling none of it, so the old text matcher answered "safe"
and the code reached the sandbox with no card.

The load-bearing assertions are at the *gateway* level, because the gateway is
the thing that decides whether a human is asked -- reverting it to its previous
regex behaviour turns ``test_the_gateway_asks_for_approval_on_every_obfuscated_effect``
red while the benign control stays green.
"""
import pytest
from backend.app.services.tools.effect_scan import scan_effects
from backend.app.services.tools.tool_gateway import tool_gateway

BENIGN = "print(sum(range(10)))"

# Each string achieves a process/filesystem/network effect through a form the
# old ``_SYSTEM_COMMAND_PATTERNS`` / ``_NETWORK_PATTERNS`` regexes could not see.
EVASIONS = [
    # Constant-folded attribute name: "sys" + "tem" -> system.
    'import os\ngetattr(os, "sys" + "tem")("id")',
    # __import__ returning the module, then attribute access.
    '__import__("os").system("id")',
    # importlib.import_module of a dangerous module.
    'import importlib\nimportlib.import_module("subprocess").run(["id"])',
    # exec of a literal payload, scanned recursively.
    'exec("import socket\\nsocket.socket()")',
    # eval of a literal payload.
    "eval(\"__import__('requests').get('http://x')\")",
    # from-import alias: the bound name is the effect.
    "from os import system as s\ns('id')",
    # __dict__ subscript hiding the attribute name.
    'import os\nos.__dict__["system"]("id")',
    # shell=True with no dangerous module named at all.
    'call("id", shell=True)',
]

# Unresolvable targets must fail closed: an effect we cannot rule out asks.
FAIL_CLOSED = [
    'getattr(module, "system")("id")',          # object has no import behind it
    'import os\ngetattr(os, name)("id")',       # attribute name is dynamic
    "__import__(name).run([])",                 # imported module is dynamic
    "eval(user_input)",                         # exec/eval payload is dynamic
]


@pytest.mark.parametrize("code", EVASIONS)
def test_every_obfuscated_effect_is_resolved(code):
    assert scan_effects(code).needs_approval is True


@pytest.mark.parametrize("code", FAIL_CLOSED)
def test_unresolvable_targets_fail_closed(code):
    scan = scan_effects(code)
    assert scan.unresolved is True
    assert scan.needs_approval is True


def test_the_gateway_asks_for_approval_on_every_obfuscated_effect():
    for code in EVASIONS:
        needs, reason = tool_gateway.requires_approval("execute_python", {"code": code})
        assert needs is True, code
        assert reason, code


def test_the_gateway_asks_for_approval_when_a_target_cannot_be_resolved():
    for code in FAIL_CLOSED:
        needs, _ = tool_gateway.requires_approval("execute_python", {"code": code})
        assert needs is True, code


def test_benign_code_is_still_automatic():
    assert scan_effects(BENIGN).needs_approval is False
    assert tool_gateway.requires_approval("execute_python", {"code": BENIGN}) == (False, None)


def test_a_folded_system_effect_reads_as_a_system_command():
    """The card must say *what* it saw, not merely that it saw something."""
    needs, reason = tool_gateway.requires_approval(
        "execute_python", {"code": 'import os\ngetattr(os, "sys" + "tem")("id")'}
    )
    assert needs is True
    assert "system" in (reason or "")


def test_a_folded_network_effect_reads_as_a_network_call():
    needs, reason = tool_gateway.requires_approval(
        "execute_python", {"code": 'import importlib\nimportlib.import_module("socket")'}
    )
    assert needs is True
    assert "network" in (reason or "")


def test_an_unparseable_body_has_no_effect_to_gate():
    # Code that cannot parse cannot execute, so there is no effect to approve.
    assert scan_effects("def (").needs_approval is False
