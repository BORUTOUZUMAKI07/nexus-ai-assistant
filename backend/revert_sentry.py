"""Revert harness for the ``tests/conftest.py`` SENTRY_DSN ordering fix.

Repo rule: every fix ships with a harness that applies its inverse to the real
source, runs the covering test, and requires that test to *fail* — then
restores the file byte-exactly. A test that passes with and without the fix is
not testing the fix.

The fix: ``os.environ["SENTRY_DSN"] = ""`` was moved from *after* conftest's
first backend import to *before* it. It only works by position —
``app/infrastructure/cache/redis_client.py:4`` does
``from backend.app.core.config import settings``, which builds the singleton at
import time (``config.py:740``), so an assignment made afterwards has already
been beaten to ``backend/.env``'s live 95-char DSN.

Two entries, each proving one of the two tests independently load-bearing:

``R1`` — the fix's inverse: the guard line is moved back below
    ``import redis.asyncio as redis``. Both tests must FAIL: ``test1`` because
    the singleton now really holds the DSN, ``test2`` because the ordering it
    reads is now wrong.

``R2`` — a *probe*, not an inverse: the guard keeps its correct position but
    assigns a valid-shaped offline DSN. ``test1`` must FAIL (the singleton holds
    a truthy value) while ``test2`` must PASS (the ordering is intact). That
    asymmetry is the whole point — it is what shows ``test2`` is not a duplicate
    of ``test1`` and vice versa, i.e. neither test is redundant.

Report parsing deliberately uses ``-v``'s inline ``PASSED``/``FAILED`` column
rather than ``-r`` flags: ``-r`` *replaces* the report characters, so combining
``-rf`` and ``-rs`` silently reports nothing (measured 2026-10-07). With no
``-r`` flag at all the default ``fE`` short summary is kept as a cross-check.

Usage: ``cd backend && uv run python revert_sentry.py``.
Never run it while anything else is using ``tests/conftest.py`` — it reverts a
file every test session imports.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
CONFTEST = BACKEND / "tests" / "conftest.py"
TEST_FILE = BACKEND / "tests" / "test_no_sentry_in_tests.py"
SNAPSHOT_DIR = BACKEND / ".revert_sentry_snapshot"

MODULE = "tests/test_no_sentry_in_tests.py"

GUARD = 'os.environ["SENTRY_DSN"] = ""'
# The probe must be *DSN-shaped*, not merely non-empty: main.py:44 runs
# sentry_sdk.init() during conftest's import, and an unparseable value raises
# BadDsn("Unsupported scheme ''") there, which kills collection so neither test
# ever runs and the harness reports INCONCLUSIVE instead of the asymmetry it is
# looking for (measured 2026-10-07). localhost:9 keeps it offline — refused on
# connect, and init itself makes no network call (also measured).
PROBE_GUARD = (
    'os.environ["SENTRY_DSN"] = '
    '"http://0123456789abcdef0123456789abcdef@localhost:9/1"'
)
REDIS_IMPORT = "import redis.asyncio as redis  # noqa: E402"

T1 = "test_the_settings_singleton_never_sees_the_dsn"
T2 = "test_the_sentry_guard_precedes_every_backend_import"

REVERTS = [
    (
        "R1",
        "the fix's inverse: the SENTRY_DSN guard moved back below the backend "
        "imports -> BOTH tests must fail",
        {T1: "failed", T2: "failed"},
    ),
    (
        "R2",
        "PROBE: guard keeps its correct position but assigns a valid offline "
        f"DSN -> {T1} fails, {T2} passes (proves neither test is redundant)",
        {T1: "failed", T2: "passed"},
    ),
]


# --------------------------------------------------------------------------
# the test's own ordering definition, imported rather than re-derived, so the
# harness cannot drift into verifying a second copy of the rule
# --------------------------------------------------------------------------
def _load_ordering_helper():
    spec = importlib.util.spec_from_file_location("sentry_guard_tests", TEST_FILE)
    if spec is None or spec.loader is None:
        raise SystemExit(f"HARNESS BUG: cannot load {TEST_FILE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._guard_and_import_lines


def _split(raw: bytes) -> tuple[str, str, bool]:
    """(text, newline, had trailing newline) without normalising endings."""
    nl = "\r\n" if b"\r\n" in raw else "\n"
    return raw.decode("utf-8"), nl, raw.endswith(b"\n")


def _guard_and_imports(source: str) -> tuple[list[int], list[int]]:
    return _load_ordering_helper()(ast.parse(source))


# --------------------------------------------------------------------------
# preflight / snapshot / restore — the file must never be left reverted
# --------------------------------------------------------------------------
def preflight() -> None:
    if SNAPSHOT_DIR.exists():
        raise SystemExit(
            "REFUSING TO RUN: snapshot already exists at "
            f"{SNAPSHOT_DIR}. A previous run was killed mid-revert, so "
            f"{CONFTEST.name} may currently be REVERTED on disk. Inspect and "
            "restore it before re-running (never trust a green baseline over a "
            "leaked revert)."
        )
    for path in (CONFTEST, TEST_FILE):
        if not path.exists():
            raise SystemExit(f"REFUSING TO RUN: missing {path}")
    text, _, _ = _split(CONFTEST.read_bytes())
    guards, imports = _guard_and_imports(text)
    if not guards:
        raise SystemExit(
            "REFUSING TO RUN: the fix is already absent — no SENTRY_DSN "
            "assignment found in conftest.py"
        )
    if not imports or min(guards) > min(imports):
        raise SystemExit(
            "REFUSING TO RUN: the fix already looks reverted — the guard is "
            "not before the first backend import"
        )
    if GUARD not in text:
        raise SystemExit("REFUSING TO RUN: guard line shape not found verbatim")


def snapshot() -> None:
    SNAPSHOT_DIR.mkdir(exist_ok=False)
    shutil.copy2(CONFTEST, SNAPSHOT_DIR / "conftest.py")


def restore() -> bool:
    """Put the pristine bytes back. Runs unconditionally, even after a crash."""
    pristine = SNAPSHOT_DIR / "conftest.py"
    if not pristine.exists():
        return False
    shutil.copy2(pristine, CONFTEST)
    after = CONFTEST.read_bytes()
    # compare the RESULT to the snapshot, never to the pre-restore bytes: when
    # a revert is live those differ by definition, and the first version of
    # this check reported "restored: FAILED" on every successful restore.
    return after == pristine.read_bytes()


def check_restored() -> None:
    pristine = SNAPSHOT_DIR / "conftest.py"
    if not pristine.exists():
        raise SystemExit("HARNESS BUG: snapshot missing after run")
    if CONFTEST.read_bytes() != pristine.read_bytes():
        raise SystemExit(
            f"HARNESS BUG: {CONFTEST.name} was not restored byte-exactly"
        )


# --------------------------------------------------------------------------
# applying a revert
# --------------------------------------------------------------------------
def apply(revert_id: str) -> str:
    raw = CONFTEST.read_bytes()
    text, nl, trailing = _split(raw)
    lines = text.splitlines()

    guard_idx = [i for i, ln in enumerate(lines) if ln.strip() == GUARD]
    if len(guard_idx) != 1:
        raise SystemExit(
            f"HARNESS BUG: expected exactly one guard line, found {len(guard_idx)}"
        )
    idx = guard_idx[0]
    del lines[idx]

    if revert_id == "R1":
        anchors = [i for i, ln in enumerate(lines) if ln.strip() == REDIS_IMPORT]
        if len(anchors) != 1:
            raise SystemExit(
                f"HARNESS BUG: expected exactly one redis import, found {len(anchors)}"
            )
        # only the assignment moves; its explanatory comment stays put, which
        # is exactly what a careless import reorder looks like
        lines.insert(anchors[0] + 1, GUARD)
        note = "guard assignment moved below the redis import (comment left in place)"
    else:
        lines.insert(idx, PROBE_GUARD)
        note = "guard assignment replaced in place with a DSN-shaped offline value"

    new_text = nl.join(lines) + (nl if trailing else "")

    # self-check: did the edit produce the state it claims? (a silent no-op
    # revert is the classic harness defect — cf. §9.17)
    guards, imports = _guard_and_imports(new_text)
    if not guards or not imports:
        raise SystemExit("HARNESS BUG: post-edit parse found no guard/import")
    if revert_id == "R1":
        if min(guards) < min(imports):
            raise SystemExit("HARNESS BUG: R1 did not actually move the guard")
    else:
        if min(guards) > min(imports):
            raise SystemExit("HARNESS BUG: R2 moved the guard (it must not)")
        if PROBE_GUARD not in new_text or GUARD in new_text:
            raise SystemExit("HARNESS BUG: R2 did not swap the value")

    CONFTEST.write_text(new_text, encoding="utf-8", newline="")
    if CONFTEST.read_bytes().decode("utf-8").count(nl) == 0:
        raise SystemExit("HARNESS BUG: newline survived no lines")
    return note


# --------------------------------------------------------------------------
# running the tests
# --------------------------------------------------------------------------
def run_tests() -> dict[str, str]:
    cmd = [sys.executable, "-m", "pytest", MODULE, "-v", "--tb=short", "-p", "no:randomly"]
    proc = subprocess.run(
        cmd,
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")

    # -v inline column:  tests\test_x.py::test_name PASSED  [ 50%]
    inline = re.findall(
        r"(?m)^tests[/\\]\S+?::(\S+)\s+(PASSED|FAILED|SKIPPED|ERROR)\b", out
    )
    status = {name: state.lower() for name, state in inline}
    if not status:
        # cross-check: the default short summary (no -r flag = default fE)
        for name in re.findall(r"(?m)^FAILED\s+\S+?::(\S+)", out):
            status[name] = "failed"
    if not status:
        # A harness that cannot say *why* it saw nothing is worse than none:
        # "no results parsed" has to be distinguishable from "the session
        # crashed before collection", which is exactly what R2 did the first
        # time (BadDsn raised while conftest imported backend.app.main).
        print("  --- pytest tail (no results parsed) ---")
        for line in out.splitlines()[-25:]:
            print(f"    {line}")
        print("  --- end tail ---")
    return status


# --------------------------------------------------------------------------
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=== revert harness: conftest SENTRY_DSN ordering fix ===")

    preflight()
    snapshot()
    ok = True
    try:
        baseline = run_tests()
        print(f"  baseline: {baseline or '<nothing parsed>'}")
        if baseline.get(T1) != "passed" or baseline.get(T2) != "passed":
            ok = False
            print(
                "  BASELINE RED — the tests do not pass on the pristine tree, "
                "so no revert result would mean anything. Snapshot kept at "
                f"{SNAPSHOT_DIR}; conftest restored."
            )
            return 1

        for revert_id, desc, expected in REVERTS:
            print(f"\n[{revert_id}] {desc}")
            try:
                note = apply(revert_id)
                print(f"  applied: {note}")
                status = run_tests()
                print(f"  observed: {status or '<nothing parsed>'}")
                if not status:
                    print("  INCONCLUSIVE: no test results parsed")
                    ok = False
                    continue
                got = {name: status.get(name, "not-run") for name in expected}
                if got == expected:
                    print(f"  {revert_id} LOAD-BEARING (as required)")
                else:
                    ok = False
                    print(f"  {revert_id} NOT LOAD-BEARING")
                    for name, want in expected.items():
                        mark = "ok" if got[name] == want else "WRONG"
                        print(f"      {mark}: {name} wanted={want} got={got[name]}")
            except SystemExit as exc:
                ok = False
                print(f"  HARNESS ERROR: {exc}")
            finally:
                restored = restore()
                print(f"  restored: {'byte-exact' if restored else 'FAILED'}")
    finally:
        if not restore():
            print(f"  WARNING: could not restore {CONFTEST}")
        if SNAPSHOT_DIR.exists():
            try:
                check_restored()
                shutil.rmtree(SNAPSHOT_DIR)
            except SystemExit as exc:
                ok = False
                print(f"  {exc}")

    print(f"\nRESULT: {'ALL REVERTS LOAD-BEARING' if ok else 'NOT LOAD-BEARING'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
