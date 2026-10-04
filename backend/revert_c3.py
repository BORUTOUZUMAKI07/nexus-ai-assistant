"""Revert harness for the C3 evidence work (backend half).

Applies the inverse of each fix to the real source, runs the tests that claim to
cover it, and requires them to FAIL. The frontend counterpart is
``frontend/revert_c3.py``.

## Why this round needed one more than the last

Every layer of the citation chain had tests and every one of them passed while
the feature did nothing. ``citation_payloads`` was tested as a function; the
wiring was a source-grep; ``add_message`` accepted the arguments and nothing
passed them. Nothing spanned the seam, so a green suite was reporting a property
the product did not have -- which is the exact failure AGENTS.md §2 and §9.27
describe, and the reason the reverts below include the *call sites* rather than
only the functions.

## The three properties this harness depends on

All learned the hard way (AGENTS.md §7, §9.17):

  * **Preflight.** If a revert is already applied in the tree, refuse to run. A
    snapshot of an already-reverted tree restores the damage instead of undoing
    it, and the harness then reports a regression it invented.
  * **Restore is unconditional.** A harness killed mid-run leaves a real source
    file reverted and the next run fails its own baseline.
  * **A revert must reintroduce the behaviour, not the identifier.** The first
    attempt at C2's B1 replaced a call site only and every covering test went red
    with ``NameError``, which proves the name is spelled in that file and nothing
    about the wire format.

Liveness for pytest is the number of tests the ``-k`` filter *selected*
(passed + failed + errors), never the number that passed -- a load-bearing revert
is expected to leave zero passing tests.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND = Path(__file__).resolve().parents[0]

EXECUTOR = Path("app/services/run_executor.py")
ROUTE = Path("app/api/v1/conversations.py")
CONV_REPO = Path("app/domain/conversation/repository.py")

CITATIONS = "tests/test_run_citations.py"
IDENTITY = "tests/test_run_message_identity.py"

# (id, file, description, [(pristine, reverted), ...], test file, pytest -k filter)
REVERTS: list[tuple[str, Path, str, list[tuple[str, str]], str, str]] = [
    (
        "C1",
        EXECUTOR,
        "a finished run emits its citations as frames",
        [
            (
                "        frames.extend(citation_payloads(snapshot.values))\n",
                "        # reverted: the run reports a verdict and a score, never its evidence\n",
            )
        ],
        CITATIONS,
        "executor_publishes_the_citations_to_the_run_log",
    ),
    (
        "C2",
        EXECUTOR,
        "the message row keeps the citations the client was shown",
        [
            (
                "                citations=citations or [],\n",
                "                # reverted: the column keeps its [] default\n",
            )
        ],
        CITATIONS,
        "the_message_row_keeps_the_evidence",
    ),
    (
        "C3",
        EXECUTOR,
        "the message row keeps the tool calls the run made",
        [
            (
                "                tool_calls=tool_calls or [],\n",
                "                # reverted: the column keeps its [] default\n",
            )
        ],
        CITATIONS,
        "the_message_row_keeps_the_evidence",
    ),
    (
        "C4",
        EXECUTOR,
        "tool calls are collected from the frames as they pass through",
        [
            (
                '                    elif frame.get("type") == "tool_call":\n',
                '                    elif frame.get("type") == "never-emitted":\n',
            )
        ],
        CITATIONS,
        # NOT `the_message_row_keeps_the_evidence`: that test passes `tool_calls`
        # in as an argument, so it never exercises the collection. The first
        # version of this filter was that test, and the revert came back green --
        # the harness reporting a claim nothing was checking, which is the one
        # outcome it exists to prevent.
        "executor_stores_the_tool_calls",
    ),
    (
        "C5",
        EXECUTOR,
        "a run that died mid-answer still stores the evidence it had",
        [
            (
                "                    citations=await _run_citations(graph, config),\n",
                "                    # reverted: a failed run keeps nothing\n",
            )
        ],
        CITATIONS,
        "a_run_that_dies_mid_answer_still_stores_what_it_had",
    ),
    (
        "C6",
        ROUTE,
        "the rejoin response names the message the run persisted",
        [
            (
                "    persisted = {MESSAGE_ID_HEADER: str(run.message_id)} if run.message_id else {}\n",
                "    persisted = {}  # reverted: the client is never told\n",
            ),
            (
                "            **persisted,\n",
                "",
            ),
        ],
        IDENTITY,
        "the_rejoin_response_names_the_message",
    ),
    (
        "C7",
        CONV_REPO,
        "the transcript read selects the *newest* window",
        [
            (
                "        ascending = (Message.created_at.asc(), Message.id.asc())  # type: ignore[attr-defined]\n"
                "        if newest:\n",
                "        ascending = (Message.created_at.asc(), Message.id.asc())  # type: ignore[attr-defined]\n"
                "        if False:\n",
            )
        ],
        IDENTITY,
        "newest_window",
    ),
    (
        "C8",
        CONV_REPO,
        "the newest window is chosen by a subquery, not by the outer order",
        [
            (
                "            statement = (\n"
                "                select(Message).where(Message.id.in_(window)).order_by(*ascending)\n"
                "            )\n",
                "            statement = (\n"
                "                select(Message)\n"
                "                .where(Message.conversation_id == conversation_id)\n"
                "                .order_by(*ascending)\n"
                "                .limit(limit)\n"
                "            )\n",
            )
        ],
        IDENTITY,
        "newest_window or membership_not_exclusion",
    ),
    (
        "C9",
        CONV_REPO,
        "the window is matched by membership, not by exclusion",
        [
            (
                "                select(Message).where(Message.id.in_(window)).order_by(*ascending)\n",
                "                select(Message)\n"
                "                .where(Message.id.not_in(window))\n"
                "                .order_by(*ascending)\n",
            )
        ],
        IDENTITY,
        "membership_not_exclusion",
    ),
    (
        "C10",
        CONV_REPO,
        "both sides of the window carry the id tiebreaker",
        [
            (
                "        ascending = (Message.created_at.asc(), Message.id.asc())  # type: ignore[attr-defined]\n",
                "        ascending = (Message.created_at.asc(),)  # type: ignore[attr-defined]\n",
            ),
            (
                "                .order_by(Message.created_at.desc(), Message.id.desc())  # type: ignore[attr-defined]\n",
                "                .order_by(Message.created_at.desc())  # type: ignore[attr-defined]\n",
            ),
        ],
        IDENTITY,
        "tiebreaker",
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
        line.strip()[len("FAILED "):]
        for line in out.splitlines()
        if line.strip().startswith("FAILED ")
    ]
    tail = [ln for ln in out.splitlines() if "passed" in ln or "failed" in ln]
    print(f"    (selected={selected} exit={proc.returncode}) {tail[-1] if tail else ''}")
    for name in failed_names:
        print(f"    failed: {name}")
    if not failed_names and proc.returncode != 0:
        print("    " + (proc.stderr or out)[-900:])
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
                problems.append(f"{rid}: pristine text is not unique -- {desc}")

    if problems:
        print("PREFLIGHT FAILED -- refusing to touch the tree:")
        for p in problems:
            print("  " + p)
        print("\nIf one of these reverts is already applied, restore it by hand first.")
        return 2

    # ── Baseline ────────────────────────────────────────────────────────────
    # Both new files, not just the first one: C6 covers a route and the rest
    # cover the executor, and a baseline of one file says nothing about the other.
    print("baseline (nothing reverted) ...", flush=True)
    for test_file in (CITATIONS, IDENTITY):
        green = run_tests(test_file, "")
        if green is not True:
            print(f"  baseline is NOT green on {test_file}; nothing was touched.")
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