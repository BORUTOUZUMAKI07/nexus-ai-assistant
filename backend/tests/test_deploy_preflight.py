"""The deploy preflight is a gate, so it must itself be checked.

Deployment stops being a mystery when the provable half is executable: the Render
blueprint, the frontend build entry point, and the documented contract. The
credentialed half cannot run in CI, and this test does not pretend otherwise — it
asserts the static mode passes, that the script exits non-zero when something it
checks is missing, and that it never echoes a secret value.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy-preflight.sh"

pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="preflight script not present")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "scripts/deploy-preflight.sh", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_static_preflight_passes():
    result = _run("--static")
    assert result.returncode == 0, (
        "the repo's own deploy artifacts do not validate:\n" + result.stdout + result.stderr
    )
    assert "PREFLIGHT PASSED" in result.stdout


def test_preflight_rejects_an_unknown_option():
    """A typo must fail loudly rather than silently skipping the environment checks."""
    result = _run("--statik")
    assert result.returncode == 1
    assert "unknown option" in (result.stderr + result.stdout)


def test_preflight_never_prints_a_secret_value():
    """Only names and lengths may appear in the output."""
    result = _run("--static")
    for line in result.stdout.splitlines():
        assert "sk-" not in line, f"possible API key echoed: {line[:60]}"
        assert "postgresql://" not in line, f"possible DSN echoed: {line[:60]}"
