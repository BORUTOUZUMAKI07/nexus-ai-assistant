"""Revert harness for the LangSmith region fix (L1-L4).

Each entry applies the *inverse* of one fix to the real source, runs the tests
that are supposed to catch it, and requires them to FAIL. A test that passes with
and without the fix is not testing the fix.

Run from backend/:   uv run python revert_ls.py

Rules this harness obeys, because each was learned by breaking one:
  * Liveness is "tests were selected", never "tests passed". A load-bearing
    revert is *expected* to leave zero passing tests.
  * Snapshot pristine sources first and restore them unconditionally, including
    when the baseline is already red. A killed run leaves real source reverted
    and `git diff` cannot see it if the file is untracked.
  * A preflight reports which reverts are already applied and refuses to touch
    anything, so a snapshot of an already-reverted tree cannot restore damage.
  * Never run two harnesses against one tree.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
REPO = BACKEND.parent

CONFIG = BACKEND / "app" / "core" / "config.py"
ENV_EXAMPLE = BACKEND / ".env.example"
TEST = BACKEND / "tests" / "test_langsmith_region.py"

# Marker left behind by a killed run, per AGENTS.md §7.
MARKER = "# reverted:"

SNAPSHOT = BACKEND / ".revert_ls_snapshot"


def run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        cmd, cwd=BACKEND, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return proc.returncode, proc.stdout + proc.stderr


def pytest_filtered(*tests: str) -> tuple[int, str]:
    return run(["uv", "run", "pytest", *tests, "-p", "no:randomly", "-q", "--no-header"])


def selected_tests(output: str) -> int:
    """How many tests the run collected. Never numPassedTests.

    A load-bearing revert breaks the one test the filter selects, so zero
    passing is the expected outcome and asserting on it would invert the result.
    """
    for line in output.splitlines():
        if " passed" in line or " failed" in line or " no tests ran" in line:
            for token in line.replace("=", " ").split():
                if token.isdigit():
                    # The first count on the summary line is tests-collected.
                    return int(token)
    return 0


# ── the four reverts ──────────────────────────────────────────────────────────

def l1_remove_settings_field() -> None:
    """Delete the LANGSMITH_ENDPOINT declaration from core.config.Settings."""
    text = CONFIG.read_text(encoding="utf-8")
    start = text.index("    # Which LangSmith *deployment* to talk to.")
    end = text.index("    LANGSMITH_ENDPOINT: str | None = None\n") + len(
        "    LANGSMITH_ENDPOINT: str | None = None\n"
    )
    CONFIG.write_text(text[:start] + text[end:], encoding="utf-8")


def l2_remove_example_variable() -> None:
    """Delete the LANGSMITH_ENDPOINT= line from .env.example.

    Matches the whole line with its newline stripped, so it works in a CRLF
    checkout. Anchoring on the bare prefix would also match the word
    "LANGSMITH_ENDPOINT" inside the surrounding comment block and delete the
    documentation instead of the variable - which L2 would then report as
    load-bearing for the wrong reason.
    """
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if not ln.startswith("LANGSMITH_ENDPOINT=")]
    assert len(kept) == len(lines) - 1, "expected exactly one LANGSMITH_ENDPOINT= line"
    ENV_EXAMPLE.write_text("".join(kept), encoding="utf-8")


def l3_strip_default_host_from_example_comment() -> None:
    """Remove every api.smith.langchain.com occurrence from .env.example.

    The prose names which host is the SDK's default, because an operator who
    does not know it is the default cannot recognise the config that caused the
    403.

    Every occurrence, not just the GCP US table row: the sibling rows for
    eu./apac./aws. all *contain* the substring "api.smith.langchain.com". The
    first version of this revert deleted only the GCP US row, and the test still
    passed - three siblings satisfied a substring search that was meant to prove
    the default was named. That is a test that can pass for the wrong reason,
    which is worse than no test at all.
    """
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "api.smith.langchain.com" in text, "preflight: default host already absent"
    kept = [ln for ln in text.splitlines(keepends=True) if "api.smith.langchain.com" not in ln]
    assert len(kept) < len(text.splitlines(keepends=True)), "preflight: nothing removed"
    ENV_EXAMPLE.write_text("".join(kept), encoding="utf-8")


def l4_point_example_at_the_ui_host() -> None:
    """Set LANGSMITH_ENDPOINT to the aws.smith.langchain.com UI host.

    The realistic hand-edit error: the human knows the traces live behind that
    URL, and types it. It looks correct and fails differently from the original.

    Matches on a whole line and writes back with the file's own newline. The
    first version searched for the literal "LANGSMITH_ENDPOINT=\\n", which never
    matches in a CRLF checkout - the revert silently became a no-op and then
    tripped its own preflight, reporting a harness fault as if it were a finding.
    """
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines(keepends=True)
    hits = [i for i, ln in enumerate(lines) if ln.rstrip("\r\n").strip() == "LANGSMITH_ENDPOINT="]
    assert len(hits) == 1, f"preflight: expected one empty LANGSMITH_ENDPOINT= line, got {len(hits)}"
    i = hits[0]
    newline = "\r\n" if lines[i].endswith("\r\n") else "\n"
    lines[i] = f"LANGSMITH_ENDPOINT=https://aws.smith.langchain.com{newline}"
    ENV_EXAMPLE.write_text("".join(lines), encoding="utf-8")


REVERTS = [
    ("L1", "Settings loses the LANGSMITH_ENDPOINT field",
     l1_remove_settings_field,
     ["tests/test_langsmith_region.py::test_langsmith_endpoint_is_declared_in_settings"]),
    ("L2", ".env.example loses the LANGSMITH_ENDPOINT variable",
     l2_remove_example_variable,
     ["tests/test_langsmith_region.py::test_env_example_ships_the_endpoint_variable"]),
    ("L3", ".env.example stops naming the SDK's default host",
     l3_strip_default_host_from_example_comment,
     ["tests/test_langsmith_region.py::test_env_example_names_the_sdk_default_region_as_a_trap"]),
    ("L4", ".env.example points LANGSMITH_ENDPOINT at the smith. UI host",
     l4_point_example_at_the_ui_host,
     ["tests/test_langsmith_region.py::test_env_example_documented_host_is_an_api_host"]),
]

# Only the files a revert actually modifies. TEST is deliberately absent: a
# harness that restores a test file it never touched is a harness that will one
# day restore it over a real edit.
TOUCHED = (CONFIG, ENV_EXAMPLE)


def preflight() -> None:
    """Refuse to run if any revert already looks applied."""
    problems = []
    if "LANGSMITH_ENDPOINT: str | None = None" not in CONFIG.read_text(encoding="utf-8"):
        problems.append("L1 already applied (no LANGSMITH_ENDPOINT field in config.py)")
    example = ENV_EXAMPLE.read_text(encoding="utf-8")
    if "LANGSMITH_ENDPOINT=\n" not in example:
        problems.append("L2/L4 already applied (LANGSMITH_ENDPOINT= has a value)")
    if "api.smith.langchain.com" not in example:
        problems.append("L3 already applied (default host absent from .env.example)")
    for path in TOUCHED:
        if MARKER in path.read_text(encoding="utf-8"):
            problems.append(f"revert marker found in {path.name} - a previous run was killed")
    if problems:
        print("PREFLIGHT FAILED - refusing to touch anything:")
        for problem in problems:
            print(f"  - {problem}")
        print("\nRestore the sources, then re-run.")
        sys.exit(2)


def snapshot() -> None:
    SNAPSHOT.mkdir(exist_ok=True)
    for path in TOUCHED:
        (SNAPSHOT / path.name).write_bytes(path.read_bytes())


def restore() -> None:
    """Put the pristine sources back.

    Does NOT remove SNAPSHOT. The first version of this function deleted the
    snapshot after restoring, so every restore after the first revert was a
    silent no-op: the baseline file was never touched again, each revert
    compounded on the previous one's damage, and L4 then aborted on its own
    preflight with `.env.example` still reverted.

    The symptom that makes this class of bug expensive: the harness reported a
    real-looking result ("L3: NOT LOAD-BEARING") produced against a tree nobody
    intended. It also left real source reverted on disk. Deleting the snapshot
    is the harness's own cleanup step and belongs at the very end of main(),
    never inside the per-revert restore.
    """
    missing = [p.name for p in TOUCHED if not (SNAPSHOT / p.name).exists()]
    if missing:
        raise SystemExit(
            f"REFUSING to restore - snapshot is missing {missing}. The tree may be "
            "left reverted. Restore these from git before running anything else."
        )
    for path in TOUCHED:
        path.write_bytes((SNAPSHOT / path.name).read_bytes())


def cleanup_snapshot() -> None:
    if SNAPSHOT.exists():
        for leftover in SNAPSHOT.iterdir():
            leftover.unlink()
        SNAPSHOT.rmdir()


def main() -> None:
    preflight()

    code, output = pytest_filtered("tests/test_langsmith_region.py")
    if code != 0:
        print("BASELINE IS RED - not attributing a failure to a revert.\n")
        print(output[-3000:])
        sys.exit(1)
    print(f"baseline green ({selected_tests(output)} tests selected)\n")

    snapshot()
    results = []
    try:
        for tag, description, revert, tests in REVERTS:
            revert()
            code, output = pytest_filtered(*tests)
            failed = [ln for ln in output.splitlines() if ln.startswith("FAILED")]
            load_bearing = code != 0 and bool(failed)
            status = "LOAD-BEARING" if load_bearing else "NOT LOAD-BEARING"
            results.append((tag, status))
            print(f"{tag}: {description}")
            print(f"    {status}")
            if not load_bearing:
                print(f"    exit={code} selected={selected_tests(output)}")
                print("    " + " ".join(output.splitlines()[-6:]))
            else:
                for line in failed:
                    print(f"    {line}")
            print()
            # Restore after EVERY revert, not just at the end: the next revert
            # must apply to pristine sources or it is testing the previous
            # revert's damage instead of its own fix.
            restore()
    finally:
        # Unconditional: a raised exception must not leave real sources reverted.
        restore()

    # Byte-for-byte confirmation, independent of the tests. The tests passing is
    # necessary but not sufficient - they only cover what they assert, and the
    # first version of this harness left .env.example reverted while the suite
    # was green because the variable it removed was covered by one test that a
    # later revert had already satisfied.
    for path in TOUCHED:
        if path.read_bytes() != (SNAPSHOT / path.name).read_bytes():
            print(f"POST-RUN MISMATCH: {path.name} was not restored byte-for-byte.")
            print("Restore it from git before running anything else.")
            sys.exit(1)

    code, output = pytest_filtered("tests/test_langsmith_region.py")
    if code != 0:
        print("POST-RUN BASELINE IS RED - restore failed, inspect the tree.\n")
        print(output[-3000:])
        sys.exit(1)

    # Snapshot cleanup happens here and nowhere else. See restore() for why.
    cleanup_snapshot()

    good = sum(1 for _, status in results if status == "LOAD-BEARING")
    print(f"{good}/{len(results)} load-bearing")
    sys.exit(0 if good == len(results) else 1)


if __name__ == "__main__":
    main()