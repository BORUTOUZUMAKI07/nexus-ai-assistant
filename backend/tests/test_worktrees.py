"""Worktree isolation must be proven, not asserted in a comment.

Two agents sharing one checkout collide at the file level and the collision is
silent. `scripts/worktree.sh` gives each task its own working directory; this
test is what stops that claim from being decorative. It runs the script's own
`check` mode, which creates a real worktree, writes a file into it, and fails if
the main checkout can see that file.

Skipped when git is unavailable or the checkout is not a repository (a source
tarball), because worktrees are a git feature and a skip is honest there.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "worktree.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(),
    reason="git worktrees need git and a real checkout",
)


def test_worktree_isolation_is_verified():
    """Delegates to the script: one definition of \"isolation works\", two callers."""
    assert SCRIPT.exists(), f"worktree script missing: {SCRIPT}"
    result = subprocess.run(
        ["bash", "scripts/worktree.sh", "check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        "worktree isolation check failed:\n" + result.stdout + result.stderr
    )
    assert "verified" in result.stdout.lower()


def test_worktree_script_rejects_an_unknown_subcommand():
    """A typo must fail loudly instead of silently doing nothing."""
    result = subprocess.run(
        ["bash", "scripts/worktree.sh", "list-them"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1


def test_no_probe_worktrees_are_left_behind():
    """The check cleans up after itself; a leak would poison the next run."""
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert ".isolation-probe" not in result.stdout
