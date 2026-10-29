"""Guard the architectural rule registry.

`.harness/arch-rules.json` is only useful if three things stay true:
the file parses, every rule carries an actionable what/why/fix, and the rules
actually hold on the current tree. Any one of those failing silently turns the
registry into decoration — a gate that reports nothing passes nothing.

The "why not just a shell script" answer: `check-arch.sh` is run by make and by
agents, but nothing runs *make*. This test is what makes the registry load-bearing
in CI, and it is why a new rule cannot be committed without its repair text.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
REGISTRY = ROOT / ".harness" / "arch-rules.json"
RUNNER = ROOT / "scripts" / "check-arch.sh"


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


pytestmark = pytest.mark.skipif(
    not REGISTRY.exists(), reason="arch rule registry not present in this checkout"
)


def test_registry_parses_and_is_not_empty():
    data = _registry()
    assert data.get("rules"), "an empty rule registry gates nothing"
    assert isinstance(data.get("version"), int)


def test_every_rule_carries_an_actionable_repair_instruction():
    """WHAT/WHY/FIX is the whole point: a verdict the agent cannot act on is noise.

    Asserted per rule rather than once, so the failure message names the rule
    that is missing text instead of the registry as a whole.
    """
    for rule in _registry()["rules"]:
        for field in ("id", "what", "why", "fix", "pattern", "paths"):
            assert rule.get(field), f"rule {rule.get('id', '?')} is missing '{field}'"
        # A fix that only restates the rule is not a fix.
        assert len(rule["fix"]) > 20, f"rule {rule['id']} has a placeholder fix"
        assert rule["fix"] != rule["what"], f"rule {rule['id']} repeats what as fix"


def test_rule_ids_are_unique():
    ids = [r["id"] for r in _registry()["rules"]]
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"duplicate rule ids: {sorted(duplicates)}"


def test_every_rule_path_exists():
    """A rule pointing at a moved directory silently checks zero files."""
    for rule in _registry()["rules"]:
        for path in rule["paths"]:
            assert (ROOT / path).exists(), f"rule {rule['id']} points at missing path {path}"


def test_the_tree_satisfies_every_rule():
    """The gate itself must be green at HEAD, or its red means nothing.

    Runs the real runner rather than re-implementing the match, so this test
    cannot drift from what `make check-arch` executes.
    """
    if not RUNNER.exists():  # pragma: no cover - runner ships with the registry
        pytest.skip("check-arch.sh not present")
    # Relative, not `str(RUNNER)`: on Windows the absolute path is `C:\...`,
    # which Git Bash cannot open (it fails with exit 127 "No such file"), so the
    # test would report a rule violation that is really a path-translation bug.
    result = subprocess.run(
        ["bash", "scripts/check-arch.sh"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        "architectural rules are violated:\n"
        + (result.stdout or "")[-4000:]
        + (result.stderr or "")[-1000:]
    )
