"""Revert harness for the C2 run/rejoin work (backend half).

Applies the inverse of each fix to the real source, runs the tests that claim to
cover it, and requires them to FAIL. A test that passes with and without the fix
is not testing the fix -- the suite is then reporting a property the code does
not have, which is precisely how the bug in B1 shipped: four layers of tests,
each green, each testing one link in a chain none of them spanned.

The frontend counterpart is ``frontend/revert_c2.py``; it covers the hook and the
BFF route, and this one covers the run log, the executor, the repository and the
two HTTP routes.

Three properties this harness depends on, all of them learned the hard way
(AGENTS.md sections 7 and 9.17):

  * Preflight. If a revert is already applied in the tree, refuse to run. A
    snapshot of an already-reverted tree restores the damage instead of undoing
    it, and the harness then reports a regression it invented.
  * Restore is unconditional. A harness killed mid-run leaves a real source file
    reverted, and the next run fails its own baseline.
  * A revert must reintroduce the *behaviour*, not just the identifier. The first
    attempt at B1 replaced the call site only; all three tests went red with
    ``NameError: name 'finished_run_events' is not defined``, which proves the
    name is spelled in that file and nothing whatsoever about the wire format.

Liveness for pytest is the number of tests the ``-k`` filter *selected*
(passed + failed + errors), never the number that passed. A load-bearing revert
is expected to leave zero passing tests, because the test it breaks is often the
only one the filter selects -- the same trap that made an earlier version of the
frontend harness discard exactly the result it was built to produce.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND = Path(__file__).resolve().parents[0]

EXECUTOR = Path("app/services/run_executor.py")
RUN_LOG = Path("app/services/run_log.py")
REPO = Path("app/domain/run/repository.py")
ROUTE = Path("app/api/v1/conversations.py")

REJOIN = "tests/test_run_rejoin.py"
WIRING = "tests/test_batch_a_wiring.py"

# (id, file, description, [(pristine, reverted), ...], test file, pytest -k filter)
REVERTS: list[tuple[str, Path, str, list[tuple[str, str]], str, str]] = [
    (
        "B1",
        EXECUTOR,
        "the run log stores frame objects, not pre-rendered SSE strings",
        [
            (
                "from backend.app.services.run_events import finished_run_payloads",
                "from backend.app.services.run_events import finished_run_events",
            ),
            (
                "        frames.extend(finished_run_payloads(snapshot.values))\n",
                "        frames.extend(finished_run_events(snapshot.values))\n",
            ),
        ],
        REJOIN,
        "persisted_frame_is_an_object or three_post_run_frames or old_wire_format",
    ),
    (
        "B2",
        RUN_LOG,
        "an out-of-order live frame is held, not discarded",
        [
            (
                "                pending = item\n                continue\n",
                "                continue  # reverted: the frame is dropped\n",
            )
        ],
        REJOIN,
        "a_dropped_live_frame_is_recovered",
    ),
    (
        "B3",
        EXECUTOR,
        "the final batch is flushed before the status flips",
        [
            (
                "    await writer.flush()\n"
                "    try:\n"
                "        async with session_factory() as session:\n"
                "            await RunService(session).finish(\n"
                "                run_id,\n"
                "                status=status,\n"
                "                message_id=message_id,\n"
                "                error=error,\n"
                "                event_count=writer.persisted,\n"
                "            )\n"
                "    except Exception as exc:\n",
                "    try:\n"
                "        async with session_factory() as session:\n"
                "            await RunService(session).finish(\n"
                "                run_id,\n"
                "                status=status,\n"
                "                message_id=message_id,\n"
                "                error=error,\n"
                "                event_count=writer.persisted,\n"
                "            )\n"
                "        await writer.flush()  # reverted: after the status flip\n"
                "    except Exception as exc:\n",
            )
        ],
        REJOIN,
        "terminal_only_after_every_frame_is_durable",
    ),
    (
        "B4",
        EXECUTOR,
        "a run that dies mid-answer still persists the partial reply",
        [
            (
                "                message_id = await _persist_reply(\n"
                "                    conversation_id=conversation_id,\n"
                "                    user_id=user_id,\n"
                "                    org_id=org_id,\n"
                "                    user_messages=user_messages,\n"
                "                    emitted_text=emitted_text,\n"
                "                    latency_ms=(time.time() - start_time) * 1000,\n"
                "                    parent_message_id=parent_message_id,\n"
                "                    run_id=run_id,\n"
                "                    session_factory=session_factory,\n"
                "                )\n"
                '                await writer.emit({"type": "error", "message": str(exc)})\n',
                "                message_id = None  # reverted: a failed run stores nothing\n"
                '                await writer.emit({"type": "error", "message": str(exc)})\n',
            )
        ],
        REJOIN,
        "a_graph_failure_becomes_an_error_frame",
    ),
    (
        "B5",
        RUN_LOG,
        "a stream ends on the run's status, never on going quiet",
        [
            (
                '        return status is None or status.status != "running"\n',
                "        return True  # reverted: every stream ends at the first quiet poll\n",
            )
        ],
        REJOIN,
        "a_quiet_stream_does_not_end_the_read",
    ),
    (
        "B6",
        REPO,
        "the replay cursor is strictly greater than what the client has",
        [
            (
                "            .where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)\n",
                "            .where(RunEvent.run_id == run_id, RunEvent.seq >= after_seq)\n",
            )
        ],
        REJOIN,
        "replay_cursor_is_strictly_greater",
    ),
    (
        "B7",
        REPO,
        "the ownership check filters on user_id in the same statement",
        [
            (
                "            select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user_id)\n",
                "            select(AgentRun).where(AgentRun.id == run_id)\n",
            )
        ],
        REJOIN,
        "ownership_check_filters_user_id",
    ),
    (
        "B8",
        ROUTE,
        "an unparseable Last-Event-ID replays everything",
        [
            (
                "    if not raw:\n        return 0\n    try:\n"
                '        return max(0, int(raw.strip()))\n'
                "    except (TypeError, ValueError):\n        return 0\n",
                "    if not raw:\n        return 0\n    try:\n"
                "        return max(0, int(raw.strip()))\n"
                "    except (TypeError, ValueError):\n"
                "        return 1  # reverted: skip a frame\n",
            )
        ],
        REJOIN,
        "last_event_id_falls_back_to_replaying_everything",
    ),
    (
        "B9",
        ROUTE,
        "the session factory is passed explicitly, not taken from a default",
        [
            (
                "            # Passed explicitly rather than left to the executor's default\n"
                "            # argument: a default is bound at import time, so patching the module\n"
                "            # global would not reach it and the test seam would look present but\n"
                "            # do nothing.\n"
                "            session_factory=async_session_factory,\n",
                "",
            )
        ],
        WIRING,
        "executor_through_its_own_session_factory",
    ),
]

_COUNT = re.compile(r"(\d+) (failed|passed|error|errors|deselected|skipped)")


def _selected(stdout: str) -> int:
    """How many tests the ``-k`` filter actually ran.

    Read off pytest's own summary line rather than the exit code: a collection
    error and a green run both exit non-zero in different ways, and neither tells
    us whether the filter matched anything.
    """
    got = {m.group(2): int(m.group(1)) for m in _COUNT.finditer(stdout)}
    # A test that errors during setup still ran, and pytest reports it as "error".
    return got.get("passed", 0) + got.get("failed", 0) + got.get("error", 0)


def run_tests(test_file: str, test_filter: str) -> bool | None:
    """True = every selected test passed. False = something failed. None = unknown."""
    proc = subprocess.run(
        [
            "uv", "run", "pytest", test_file,
            "-q", "--no-header", "-p", "no:cacheprovider", "-p", "no:randomly",
            "-rf", "-k", test_filter,
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = proc.stdout or ""
    selected = _selected(out)
    failed_names = [
        line.strip()[len("FAILED ") :]
        for line in out.splitlines()
        if line.strip().startswith("FAILED ")
    ]
    tail = [ln for ln in out.splitlines() if "passed" in ln or "failed" in ln]
    print(f"    (selected={selected} exit={proc.returncode}) {tail[-1] if tail else ''}")
    for name in failed_names:
        print(f"    failed: {name}")
    if not failed_names and proc.returncode != 0:
        print("    " + (proc.stderr or out)[-800:])
    if selected == 0:
        # Nothing ran, so a green result is vacuous. Reporting this as a pass
        # would let a filter typo look like a load-bearing revert.
        return None
    return not failed_names


def main() -> int:
    # ── Preflight ───────────────────────────────────────────────────────────
    originals: dict[str, str] = {}
    problems: list[str] = []
    for rid, rel, desc, edits, _, _ in REVERTS:
        path = BACKEND / rel
        text = path.read_text(encoding="utf-8")
        originals[rid] = text
        for pristine, _ in edits:
            if pristine not in text:
                problems.append(f"{rid}: pristine text absent -- {desc}")
            elif text.count(pristine) != 1:
                problems.append(f"{rid}: pristine text not unique -- {desc}")

    if problems:
        print("PREFLIGHT FAILED -- refusing to touch the tree:")
        for p in problems:
            print("  " + p)
        print("\nIf one of these reverts is already applied, restore it by hand first.")
        return 2

    # ── Baseline ────────────────────────────────────────────────────────────
    print("baseline (nothing reverted) ...", flush=True)
    green = run_tests(REJOIN, "")
    if green is not True:
        print("  baseline is NOT green; nothing was touched.")
        return 2
    print("  green\n", flush=True)

    results: list[tuple[str, str, str, str]] = []
    try:
        for rid, rel, desc, edits, test_file, test_filter in REVERTS:
            path = BACKEND / rel
            text = path.read_text(encoding="utf-8")
            for pristine, reverted in edits:
                assert pristine in text, rid
                text = text.replace(pristine, reverted, 1)
            path.write_text(text, encoding="utf-8")

            green = run_tests(test_file, test_filter)
            verdict = {False: "LOAD-BEARING", True: "NOT LOAD-BEARING", None: "INCONCLUSIVE"}[
                green
            ]
            results.append((rid, desc, verdict, test_filter))
            print(f"{rid}: {verdict:16} {desc}", flush=True)

            path.write_text(originals[rid], encoding="utf-8")
    finally:
        for rid, rel, _, _, _, _ in REVERTS:
            (BACKEND / rel).write_text(originals[rid], encoding="utf-8")
        print("\nrestored all sources unconditionally")

    bad = [r for r in results if r[2] != "LOAD-BEARING"]
    print(f"\n{len(results) - len(bad)}/{len(results)} load-bearing")
    for rid, desc, verdict, filt in bad:
        print(f"  !! {rid} {verdict}: {desc} (filter: {filt})")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
