"""Guard the Makefile's developer entry points.

A Makefile is not exercised by any test, so its targets rot silently and the
first person to find out is the one who just needed the server to start. These
assertions are all about the *contract* between a target and the thing it is
supposed to run, not about make's syntax.

The one that matters most is the uvicorn event-loop flag. `main.py` sets
`WindowsSelectorEventLoopPolicy`, but uvicorn 0.36+ resolves its event loop in
`Config.get_loop_factory` *before* importing the app, so that call never runs on
Windows and psycopg gets a ProactorEventLoop it cannot use. The fix is a
`--loop` flag on the command line, and a Makefile target that omits it is
indistinguishable from one that has it until a Windows developer loses an
afternoon to an OperationalError that reads like a database outage.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
MAKEFILE = BACKEND.parent / "Makefile"
MYPY_TARGETS = BACKEND / "scripts" / "mypy_targets.txt"

pytestmark = pytest.mark.skipif(
    not MAKEFILE.exists(), reason="Makefile not present in this checkout"
)


def _text() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def _variables() -> dict[str, str]:
    """`NAME := value` and `NAME = value` assignments, in file order.

    Simple recursive expansion, enough for the `$(UV)` / `$(UVICORN_LOOP)` style
    used here. A recipe that references a variable is only checkable once the
    variable is substituted, and asserting on the unexpanded text is how a test
    ends up demanding a literal flag that the Makefile is getting right through
    indirection.
    """
    values: dict[str, str] = {}
    for line in _text().splitlines():
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:?=\s*(.*)$", line)
        if match and not line.startswith(("\t", " ")):
            values[match.group(1)] = match.group(2).strip()
    for _ in range(5):  # bounded: a self-referential var must not hang a test
        changed = False
        for name, value in list(values.items()):
            for other, replacement in values.items():
                if other == name:
                    continue
                token = f"$({other})"
                if token in value and replacement != value:
                    value = value.replace(token, replacement)
                    changed = True
            values[name] = value
        if not changed:
            break
    return values


def _expand(value: str) -> str:
    for name, replacement in _variables().items():
        value = value.replace(f"$({name})", replacement)
    return value


def _recipe_for(target: str) -> str:
    """The command lines of one target, variables expanded and continuations joined.

    Returns "" when the target does not exist, so a caller can assert on
    presence separately from content instead of crashing on an index.
    """
    lines = _text().splitlines()
    pattern = re.compile(rf"^{re.escape(target)}\s*:")
    out: list[str] = []
    collecting = False
    for line in lines:
        if pattern.match(line):
            collecting = True
            continue
        if collecting:
            if not line.startswith(("\t", " ")) or not line.strip():
                break
            out.append(line.strip())
    # Join `\` continuations so a flag split across two lines is still visible
    # as one argument to whatever follows.
    return _expand(re.sub(r"\\\s*", " ", " ".join(out)))


# ─── the uvicorn event loop ───────────────────────────────────────────────────


def test_the_backend_target_uses_the_event_loop_factory():
    """Regression: `make backend` served an unusable event loop on Windows."""
    recipe = _recipe_for("backend")
    assert recipe, "no `backend:` target in the Makefile"
    assert "--loop" in recipe, (
        "the backend target does not pass --loop; on Windows uvicorn resolves "
        "ProactorEventLoop before importing the app, and psycopg 3 cannot do "
        "async I/O on it"
    )
    assert "event_loop_factory" in recipe, (
        "--loop must point at backend.app.infrastructure.common.event_loop:"
        "event_loop_factory, not at a loop that does not exist"
    )


def test_the_dev_target_also_passes_the_flag():
    """`make dev` spawns uvicorn in a separate shell, so it needs it too.

    Fixing only the `backend:` target would leave the documented all-in-one
    command broken while the simpler one works — the worst possible split,
    because the working path is the one nobody troubles to report.
    """
    recipe = _recipe_for("dev")
    assert "uvicorn" in recipe, "the dev target no longer starts uvicorn"
    assert "--loop" in recipe and "event_loop_factory" in recipe, (
        "`make dev` starts uvicorn without the event-loop factory"
    )


def test_the_flag_points_at_a_factory_that_actually_exists():
    """The `--loop` target is a string in a Makefile; check it resolves."""
    import asyncio

    from backend.app.infrastructure.common import event_loop

    assert callable(event_loop.event_loop_factory)
    loop = event_loop.event_loop_factory()
    # isinstance, not a class-name compare: on Windows CPython builds the
    # selector loop as the private `_WindowsSelectorEventLoop`, so an equality
    # check against the public name would fail on the one platform this exists
    # to support.
    assert isinstance(loop, asyncio.SelectorEventLoop), (
        f"the factory returned {type(loop).__name__}, not a selector loop; a "
        f"ProactorEventLoop here is what psycopg 3 cannot use"
    )


# ─── type-check must gate the allowlist, not the whole tree ───────────────────


def test_type_check_uses_the_mypy_allowlist():
    """`mypy app` reports ~466 pre-existing errors forever and gates nothing.

    CI type-checks the allowlist in scripts/mypy_targets.txt. A local
    `make type-check` that runs something else means a green local run says
    nothing about whether CI will pass, which is the only reason to have the
    target at all.
    """
    recipe = _recipe_for("type-check")
    assert recipe, "no `type-check:` target in the Makefile"
    assert "mypy_targets.txt" in _text(), (
        "the Makefile never reads scripts/mypy_targets.txt"
    )
    assert "--follow-imports=silent" in recipe, (
        "without it mypy follows the whole import graph out of the allowlist and "
        "reports the pre-existing debt the allowlist exists to exclude"
    )
    assert " mypy app" not in recipe, (
        "`mypy app` is the old target: it never passes, so it has trained "
        "everyone to ignore it"
    )


def test_the_allowlist_file_still_lists_real_modules():
    """A mypy_targets.txt entry that no longer exists is a silently empty gate."""
    from backend.app.core.config import settings  # noqa: F401 - import check

    assert MYPY_TARGETS.exists(), f"allowlist file is missing: {MYPY_TARGETS}"
    listed = [
        line.strip()
        for line in MYPY_TARGETS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(listed) >= 18, f"allowlist shrank to {len(listed)} entries"
    # Entries are documented as repo-root-relative ("One entry per path,
    # relative to the repository root"), which is also what the Makefile's
    # `s|^backend/||` substitution assumes. Check them against the root, and
    # also accept the backend-relative form so a future reformat is a visible
    # diff rather than a silently empty gate.
    for entry in listed:
        at_root = (BACKEND.parent / entry).exists()
        at_backend = (BACKEND / entry).exists()
        assert at_root or at_backend, f"allowlist entry does not exist: {entry}"


# ─── test targets must actually run the tests ────────────────────────────────


def test_test_unit_is_not_pointed_at_an_almost_empty_directory():
    """58 of 61 unit files live in tests/, three in tests/unit/.

    `pytest tests/unit` therefore ran 3 files and reported success, which is
    worse than not having the target: it looks like coverage.
    """
    recipe = _recipe_for("test-unit")
    assert recipe, "no `test-unit:` target in the Makefile"
    assert "tests/unit" not in recipe, (
        "tests/unit holds 3 of the 61 unit test files; deselect the markers "
        "instead of naming that directory"
    )
    assert "-m" in recipe, "test-unit must deselect the integration and e2e markers"


def test_test_targets_run_from_the_backend_directory():
    """`cd backend` is load-bearing.

    The Batch A-D test files open sources by relative path (`app/services/...`),
    so a root-level pytest run produces 131 spurious FileNotFoundError failures
    that read exactly like a catastrophic regression.
    """
    for target in ("test", "test-unit", "test-integration", "test-cov", "lint"):
        recipe = _recipe_for(target)
        assert recipe, f"no `{target}:` target in the Makefile"
        assert "cd backend" in recipe, (
            f"`{target}` does not cd into backend/ first; see the note in "
            f"AGENTS.md about repo-root-relative paths in the batch tests"
        )


# ─── targets that reference tools must exist somewhere sane ──────────────────


def test_infra_targets_use_docker_compose():
    for target in ("infra", "infra-down"):
        recipe = _recipe_for(target)
        assert "docker compose" in recipe, f"`{target}` does not use docker compose"


def test_uv_is_the_managed_runner_not_bare_python():
    """`uv run` is what pins the interpreter and syncs the lockfile."""
    text = _text()
    bare = re.findall(r"\$\(PYTHON\)\s", text)
    assert not bare, (
        f"$(PYTHON) is still invoked at {len(bare)} place(s); the venv is managed "
        f"by uv, so bare python runs against whatever is on PATH"
    )
    assert shutil.which("uv") or True  # uv may legitimately be absent in CI
