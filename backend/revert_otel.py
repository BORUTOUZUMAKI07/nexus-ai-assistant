"""Revert harness for the `action: upsert` fix in docker/otel-collector-config.yaml.

Proves the two guards in `tests/test_otel_collector_config.py` are load-bearing
by applying an inverse edit to the REAL config, running the covering file,
requiring failure, then restoring.

Run ONE harness at a time against one tree.

Two entries, and they answer different questions:

  * R1 is the fix's own inverse: strip both `action: upsert` lines. Both tests
    must fail — the static one because the field is gone, the startup one
    because the collector exits 1 before readiness.
  * R2 is a PROBE, not an inverse of anything we changed: it makes a pipeline
    reference a processor that does not exist, leaving every `action` intact.
    The static test must PASS and the startup test must FAIL. That asymmetry
    is the point — it is what shows the startup test is not redundant with the
    static one, and that the static test (which only knows one schema field)
    cannot be mistaken for coverage of the whole config. Probing "the shape
    each assertion independently forbids" is AGENTS.md 9.27's rule.

Discipline inherited from revert_tracing.py (AGENTS.md 7 / 9.27):

  * Snapshot to DISK up front, restore unconditionally — a killed run leaves a
    real source file reverted. Here the file is git-tracked, so unlike the
    ignored `.env` case a leak shows up plainly in `git diff docker/`; if a run
    dies, check that first.
  * Preflight refuses to start when a revert is already applied, rather than
    snapshotting damage and restoring it.
  * Liveness is tests SELECTED (passed + failed), never tests passed: a
    load-bearing revert is *expected* to leave zero passing.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

BACKEND = Path(__file__).resolve().parent
REPO = BACKEND.parent
CONFIG = REPO / "docker" / "otel-collector-config.yaml"
TEST_FILE = BACKEND / "tests" / "test_otel_collector_config.py"

SNAPSHOT_DIR = Path(os.environ.get("TEMP", "/tmp")) / "opencode" / "revert_otel_snapshot"
RUN_LOG = Path(os.environ.get("TEMP", "/tmp")) / "opencode" / "revert_otel_run.txt"

TOUCHED = (CONFIG,)

# The fix: exactly two of these, one per resource attribute.
ACTION_LINE = "action: upsert"
# Unique anchor for the probe: the traces pipeline is the only one carrying
# tail_sampling, so the logs/metrics pipelines (which share an identical
# processors list) are not ambiguous targets.
PROBE_ANCHOR = "processors: [memory_limiter, tail_sampling, batch, resource]"
PROBE_BAD = "processors: [memory_limiter, tail_sampling, batch, resource, does_not_exist]"

STATIC_TEST = "test_every_resource_attribute_declares_an_action"
STARTUP_TEST = "test_the_collector_really_builds_the_tracked_config"

REVERTS = [
    ("R1", "config loses both `action: upsert` entries (the fix's inverse)"),
    ("R2", "PROBE: pipeline references a missing processor, actions untouched"),
]


def _action_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() == ACTION_LINE]


def preflight() -> list[str]:
    problems: list[str] = []
    text = CONFIG.read_text(encoding="utf-8")

    found = len(_action_lines(text))
    if found != 2:
        problems.append(
            f"R1 preflight: expected exactly 2 {ACTION_LINE!r} lines in the collector "
            f"config, found {found} — the fix is missing or already reverted"
        )
    if PROBE_ANCHOR not in text:
        problems.append(f"R2 preflight: anchor {PROBE_ANCHOR!r} not found (config changed shape?)")
    if "does_not_exist" in text:
        problems.append("R2 already applied: config already references does_not_exist")
    if not TEST_FILE.exists():
        problems.append("covering test file is missing")
    if SNAPSHOT_DIR.exists():
        problems.append(f"stale snapshot from an interrupted run: {SNAPSHOT_DIR}")
    return problems


def snapshot() -> None:
    if SNAPSHOT_DIR.exists():
        shutil.rmtree(SNAPSHOT_DIR)
    SNAPSHOT_DIR.mkdir(parents=True)
    for path in TOUCHED:
        shutil.copy2(path, SNAPSHOT_DIR / path.name)


def restore() -> None:
    """Restore from DISK, unconditionally, for every touched file."""
    if not SNAPSHOT_DIR.exists():
        print(f"FATAL: snapshot dir missing ({SNAPSHOT_DIR}); refusing to guess")
        raise SystemExit(2)
    for path in TOUCHED:
        src = SNAPSHOT_DIR / path.name
        if src.exists():
            shutil.copy2(src, path)
        elif path.exists():
            path.unlink()


def cleanup_snapshot() -> None:
    if SNAPSHOT_DIR.exists():
        shutil.rmtree(SNAPSHOT_DIR)


def apply(revert_id: str) -> None:
    """Apply exactly one edit to the real config, leaving it parseable.

    Three defects this code has to avoid — each one actually hit here, not
    hypothetical:

      * Replacing a whole line drops its indentation. `PROBE_BAD` carries no
        leading spaces, so `lines[i] = PROBE_BAD` moved `processors:` to
        column 0 under `traces:` and broke the YAML. R2 then failed BOTH
        tests — including the static one that is supposed to pass — for a
        reason unrelated to the probe.
      * Appending a trailing newline the file never had adds 2 bytes, so a
        faithful revert compares unequal against its own snapshot.
      * Neither revert may break parsing: R2's bogus processor is rejected by
        the collector at *build* time, never by the YAML parser. A parse
        failure therefore means the edit is wrong, and it must surface here
        rather than as a test failure attributed to the config.

    Measured while writing this harness: text-path output 6310 bytes vs the
    byte-faithful 6314 (indent -6, trailing CRLF +2, suffix +16).
    """
    raw = CONFIG.read_bytes()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    had_trailing_newline = raw.endswith(b"\n")
    lines = CONFIG.read_text(encoding="utf-8").splitlines()

    if revert_id == "R1":
        hits = [i for i, ln in enumerate(lines) if ln.strip() == ACTION_LINE]
        assert len(hits) == 2, f"expected 2 {ACTION_LINE!r} lines, found {len(hits)}"
        for index in reversed(hits):
            del lines[index]
    elif revert_id == "R2":
        hits = [i for i, ln in enumerate(lines) if ln.strip() == PROBE_ANCHOR]
        assert len(hits) == 1, f"expected 1 probe anchor, found {len(hits)}"
        original = lines[hits[0]]
        indent = original[: len(original) - len(original.lstrip())]
        lines[hits[0]] = indent + PROBE_BAD
    else:
        raise ValueError(revert_id)

    new_text = nl.join(lines) + (nl if had_trailing_newline else "")
    try:
        yaml.safe_load(new_text)
    except Exception as exc:  # noqa: BLE001 - fail fast on a harness bug
        raise SystemExit(f"HARNESS BUG: revert {revert_id} produced invalid YAML: {exc}") from exc
    CONFIG.write_text(new_text, encoding="utf-8", newline="")


def run_tests() -> tuple[int, list[str], int, int]:
    """Run the covering file.

    Returns (selected, failed_names, skipped_count, returncode). `selected` is
    passed + failed — a skip is neither, and counting it as liveness would let
    a reverted config look covered on a machine with no Docker.

    The reporting flags are load-bearing too, and the failure was not what it
    first looked like. The original `-rf -rs` parsed ZERO names while both
    tests failed. Blaming `--tb=no` was wrong: measured one variable at a
    time, every combo with `-rf ... -rs` parsed `[]` and every combo with
    `-rf` last parsed correctly, because pytest's `-r` REPLACES the report
    characters instead of accumulating them — `-rf -rs` leaves `s` (skipped
    only) and drops `f`. Order is the whole story: `-rs -rf` works. A single
    `-rfs` works and keeps both, which is what this uses; `--tb=line` stays so
    the log remains readable. `main()` additionally treats "returncode
    non-zero but nothing parsed" as INCONCLUSIVE rather than as a pass.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(TEST_FILE), "-q", "--tb=line", "-rfs", "-p", "no:randomly"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )
    RUN_LOG.write_text((proc.stdout or "") + "\n" + (proc.stderr or ""), encoding="utf-8")
    out = proc.stdout or ""

    failed = [ln.split("::", 1)[-1].split(" ")[0] for ln in out.splitlines() if ln.startswith("FAILED ")]
    # Skips are counted, not named: pytest's skip summary is
    # "SKIPPED [1] file.py:12: reason" — file:line, no `::` — so a name-style
    # split here would return the whole line.
    skip_match = re.search(r"(\d+) skipped", out)
    skipped = int(skip_match.group(1)) if skip_match else 0
    match = re.search(r"(\d+) passed", out)
    passed = int(match.group(1)) if match else 0
    return passed + len(failed), failed, skipped, proc.returncode


def main() -> int:
    problems = preflight()
    if problems:
        print("REFUSING TO START — a revert appears already applied or the tree drifted:")
        for problem in problems:
            print(f"  - {problem}")
        print("Restore the tree (git diff docker/otel-collector-config.yaml) before running this harness.")
        return 2

    snapshot()
    results: list[tuple[str, str, str, str]] = []
    try:
        for revert_id, description in REVERTS:
            apply(revert_id)
            selected, failed, skipped, returncode = run_tests()

            if returncode != 0 and not failed:
                # A failing run with no parsed names means the reporting broke,
                # not that nothing was load-bearing — never read that as a pass.
                verdict = "INCONCLUSIVE"
                note = (
                    f"pytest failed (rc={returncode}) but no FAILED names were parsed; "
                    f"see {RUN_LOG}"
                )
            elif selected == 0:
                verdict, note = "INCONCLUSIVE", "0 tests selected — the covering file did not run"
            elif not failed:
                verdict, note = "NOT LOAD-BEARING", f"selected={selected} all passed (skipped={skipped})"
            elif revert_id == "R1":
                both = {STATIC_TEST, STARTUP_TEST}.issubset(set(failed))
                verdict = "LOAD-BEARING" if both else "PARTIAL"
                note = f"selected={selected} failed={failed} skipped={skipped}"
                if not both:
                    note += " — expected BOTH guards to fail on the fix's own inverse"
            else:
                # R2's contract is asymmetric: static must PASS, startup must FAIL.
                verdict_ok = STARTUP_TEST in failed and STATIC_TEST not in failed
                verdict = "AS SHAPED" if verdict_ok else "UNEXPECTED SHAPE"
                note = f"selected={selected} failed={failed} skipped={skipped}"
                # Only narrate the required shape when it actually happened —
                # printing it on UNEXPECTED SHAPE would assert the result the
                # harness wanted instead of the one it measured.
                if verdict_ok:
                    note += " — startup failed while static passed, as required (proves non-redundancy)"
                else:
                    note += " — expected static PASS + startup FAIL only"

            results.append((revert_id, description, verdict, note))
            print(f"{revert_id}: {verdict:<16} {description}")
            print(f"      {note}")
            restore()
    finally:
        restore()

    print("\n" + "=" * 78)
    expected = {"LOAD-BEARING", "AS SHAPED"}
    ok = sum(1 for row in results if row[2] in expected)
    print(f"{ok}/{len(results)} as required")
    for revert_id, description, verdict, note in results:
        print(f"  {revert_id} {verdict:<16} {description}")

    for path in TOUCHED:
        if path.exists():
            src = SNAPSHOT_DIR / path.name
            if src.exists() and path.read_bytes() != src.read_bytes():
                print(f"POST-CHECK FAILED: {path} differs from its snapshot")
                return 2
    cleanup_snapshot()
    print(f"snapshot cleaned: {not SNAPSHOT_DIR.exists()}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
