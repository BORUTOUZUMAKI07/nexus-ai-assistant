"""Revert harness for the "tests must not upload traces to LangSmith" fix.

Proves each part of the fix is load-bearing by applying its inverse to the real
source, running the covering tests, requiring them to FAIL, then restoring.

Run ONE harness at a time against one tree -- two live harnesses revert files
the other is restoring, and the symptoms look like three unrelated bugs.

Discipline, all learned the hard way in this repo (AGENTS.md 7 / 9.27):

  * Snapshot pristine sources to DISK up front and restore unconditionally.
    A ``finally`` does not run if the process is killed, which leaves a real
    source file reverted while ``git diff`` looks clean (the file is ignored).
    If a run dies, grep the tree for the revert markers before debugging.
  * Preflight refuses to start when a revert is already applied, instead of
    snapshotting damage and then "restoring" it.
  * Liveness is the number of tests SELECTED, never the number passed: a
    load-bearing revert is *expected* to leave zero passing tests, and reading
    "0 passed" as inconclusive discards the correct result.
  * A revert must reintroduce the behaviour. Each one here changes real bytes;
    no edit is semantically inert (a reverted test that changes nothing is the
    failure mode that hides behind a green suite).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

BACKEND = Path(__file__).resolve().parent
CONFTEST = BACKEND / "tests" / "conftest.py"
ENV_EXAMPLE = BACKEND / ".env.example"
DOTENV = BACKEND / ".env"
TEST_FILE = BACKEND / "tests" / "test_no_test_tracing.py"

SNAPSHOT_DIR = Path(os.environ.get("TEMP", "/tmp")) / "opencode" / "revert_tracing_snapshot"
TOUCHED = (CONFTEST, ENV_EXAMPLE, DOTENV)

# Module-level (zero indent) in the real conftest -- an indented anchor matches
# nothing, and the resulting count==0 assertion failure looks like a harness bug
# rather than a wrong anchor.
ASSIGN_LINE = 'os.environ["LANGSMITH_TRACING"] = "false"'

REVERTS = [
    ("R1", "conftest loses the LANGSMITH_TRACING=false override"),
    ("R3", ".env.example defaults tracing to true"),
    ("R4", "backend/.env stops asking for tracing=true"),
]

# A fourth revert, R2, removed `get_env_var.cache_clear()` from conftest and was
# measured NOT LOAD-BEARING (2026-10-07: 4 selected, all passed). The lru_cache
# on langsmith's get_env_var is only ever primed after conftest sets the value,
# because there is no conftest above this one and the test file itself does not
# import the backend. So the clear was unreachable -- an inert second guard for
# a condition `test_the_sdk_would_not_trace_from_this_session` already covers,
# since that assertion reads *through* the cache. Removed rather than kept as a
# safety net, per AGENTS.md 9.22. This note stays so the experiment is not
# repeated, and so nobody re-adds the clear believing it is doing work.


def _nl(path: Path) -> str:
    """The file's own line ending, so a CRLF checkout is restored byte-for-byte."""
    return "\r\n" if b"\r\n" in path.read_bytes() else "\n"


def preflight() -> list[str]:
    problems: list[str] = []
    text = CONFTEST.read_text(encoding="utf-8")
    if text.count(ASSIGN_LINE) != 1:
        problems.append(f"R1: expected exactly one {ASSIGN_LINE!r} in conftest, found {text.count(ASSIGN_LINE)}")
    if "LANGSMITH_TRACING=false" not in ENV_EXAMPLE.read_text(encoding="utf-8"):
        problems.append("R3 already applied: .env.example no longer defaults tracing to false")
    if DOTENV.exists() and "LANGSMITH_TRACING=true" not in DOTENV.read_text(encoding="utf-8"):
        problems.append("R4 already applied: backend/.env no longer asks for tracing=true")
    if not TEST_FILE.exists():
        problems.append("covering test file is missing")
    return problems


def snapshot() -> None:
    if SNAPSHOT_DIR.exists():
        shutil.rmtree(SNAPSHOT_DIR)
    SNAPSHOT_DIR.mkdir(parents=True)
    for p in TOUCHED:
        if p.exists():
            shutil.copy2(p, SNAPSHOT_DIR / p.name)


def restore() -> None:
    """Restore from DISK, unconditionally, for every touched file.

    Reading the snapshot out of process memory would defeat the point: the
    process that holds it is exactly the one that can be killed.
    """
    if not SNAPSHOT_DIR.exists():
        print(f"FATAL: snapshot dir missing ({SNAPSHOT_DIR}); refusing to guess")
        raise SystemExit(2)
    for p in TOUCHED:
        src = SNAPSHOT_DIR / p.name
        if src.exists():
            shutil.copy2(src, p)
        elif p.exists():
            p.unlink()


def cleanup_snapshot() -> None:
    if SNAPSHOT_DIR.exists():
        shutil.rmtree(SNAPSHOT_DIR)


def _delete_exact_line(path: Path, line: str) -> None:
    nl = _nl(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    hits = [i for i, ln in enumerate(lines) if ln.strip() == line]
    assert len(hits) == 1, f"{path.name}: expected one {line!r} line, found {len(hits)}"
    del lines[hits[0]]
    path.write_text(nl.join(lines) + nl, encoding="utf-8", newline="")


def _replace_exact_line(path: Path, old: str, new: str) -> None:
    nl = _nl(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    hits = [i for i, ln in enumerate(lines) if ln.strip() == old]
    assert len(hits) == 1, f"{path.name}: expected one {old!r} line, found {len(hits)}"
    lines[hits[0]] = new
    path.write_text(nl.join(lines) + nl, encoding="utf-8", newline="")


def apply(revert_id: str) -> None:
    """Apply exactly one inverse edit to the real source."""
    if revert_id == "R1":
        _delete_exact_line(CONFTEST, ASSIGN_LINE)
    elif revert_id == "R3":
        _replace_exact_line(ENV_EXAMPLE, "LANGSMITH_TRACING=false", "LANGSMITH_TRACING=true")
    elif revert_id == "R4":
        _replace_exact_line(DOTENV, "LANGSMITH_TRACING=true", "LANGSMITH_TRACING=false")
    else:
        raise ValueError(revert_id)


def run_tests() -> tuple[int, list[str]]:
    """Run the covering file. Returns (selected, failed_test_names)."""
    out = Path(os.environ.get("TEMP", "/tmp")) / "opencode" / "revert_tracing_run.txt"
    proc = subprocess.run(  # noqa: S603
        [
            sys.executable, "-m", "pytest", str(TEST_FILE),
            "-q", "--tb=no", "-rf", "-p", "no:randomly",
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    out.write_text((proc.stdout or "") + "\n" + (proc.stderr or ""), encoding="utf-8")

    failed = [
        ln.split("::", 1)[-1].split(" ")[0]
        for ln in (proc.stdout or "").splitlines()
        if ln.startswith("FAILED ")
    ]
    m = re.search(r"(\d+) passed", proc.stdout or "")
    total_passed = int(m.group(1)) if m else 0
    return total_passed + len(failed), failed


def main() -> int:
    problems = preflight()
    if problems:
        print("REFUSING TO START -- a revert appears already applied:")
        for p in problems:
            print(f"  - {p}")
        print("Restore the tree before running this harness.")
        return 2

    snapshot()
    results: list[tuple[str, str, str, str]] = []
    try:
        for revert_id, description in REVERTS:
            apply(revert_id)
            selected, failed = run_tests()
            if selected == 0:
                verdict = "INCONCLUSIVE"
                note = "0 tests selected -- the covering file did not run"
            elif failed:
                verdict = "LOAD-BEARING"
                note = f"selected={selected} failed={failed}"
            else:
                verdict = "NOT LOAD-BEARING"
                note = f"selected={selected} all passed"
            results.append((revert_id, description, verdict, note))
            print(f"{revert_id}: {verdict:<18} {description}")
            print(f"      {note}")
            restore()
    finally:
        restore()

    print("\n" + "=" * 78)
    load_bearing = sum(1 for r in results if r[2] == "LOAD-BEARING")
    print(f"{load_bearing}/{len(results)} load-bearing")
    for rid, desc, verdict, note in results:
        print(f"  {rid} {verdict:<18} {desc}")

    # post-check: every touched file is back to its pristine bytes
    for p in TOUCHED:
        if p.exists():
            src = SNAPSHOT_DIR / p.name
            if src.exists() and p.read_bytes() != src.read_bytes():
                print(f"POST-CHECK FAILED: {p.name} differs from its snapshot")
                return 2
    cleanup_snapshot()
    print(f"snapshot cleaned: {not SNAPSHOT_DIR.exists()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
