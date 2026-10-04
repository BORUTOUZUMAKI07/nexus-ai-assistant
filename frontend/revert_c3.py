"""Revert harness for the C3 evidence work (frontend half).

Applies the inverse of each fix to the real source, runs the tests that claim to
cover it, and requires them to FAIL. The backend counterpart is
``backend/revert_c3.py``.

## Why the frontend needs its own

The duplicate answer could not be fixed on one side. The backend knows which
message row a finished run persisted and the client knows which rows it just
hydrated, and neither fact is derivable by the other -- so the id crosses the
proxy in a header, and a name that drifts on either side of that boundary
produces a working-looking client that shows the same answer twice, forever, with
no error anywhere. Every entry below is one link in that chain.

## The three properties this harness depends on

All learned the hard way (AGENTS.md §7, §9.17):

  * **Preflight.** If a revert is already applied in the tree, refuse to run. A
    snapshot of an already-reverted tree restores the damage instead of undoing
    it, and the harness then reports a regression it invented.
  * **Restore is unconditional.** A harness killed mid-run leaves a real source
    file reverted and the next run fails its own baseline.
  * **A revert must reintroduce the behaviour, not the identifier.** The first
    attempt at C2's R1 replaced the clear with `void rememberedUnder`, which
    compiles, passes the linter and proves nothing about the clear.

Liveness is ``numTotalTests``, never ``numPassedTests``: a load-bearing revert is
*expected* to leave zero passing tests, because the test it breaks is often the
only one the filter selects. An earlier version of this harness read
``passed == 0`` as inconclusive and threw away exactly the result it was built to
produce.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FRONTEND = Path(__file__).resolve().parents[0]

HOOK = Path("src/hooks/useNexusChat.ts")
ROUTE = Path("src/app/api/chat/route.ts")
HISTORY = Path("src/lib/conversationHistory.ts")

REJOIN = "src/test/hooks/chat-rejoin.test.ts"
BFF = "src/test/chat-stream-rejoin.test.ts"
MAPPER = "src/test/lib/conversation-history.test.ts"

# (id, file, description, [(pristine, reverted), ...], [test files], vitest -t filter)
REVERTS: list[
    tuple[str, Path, str, list[tuple[str, str]], list[str], str]
] = [
    (
        "R1",
        ROUTE,
        "the BFF forwards the persisted-message id",
        [
            (
                "      backendRes.headers.get(MESSAGE_ID_HEADER)\n",
                "      null  # reverted: the header never crosses the proxy\n",
            )
        ],
        [BFF],
        "forwards the persisted-message id",
    ),
    (
        "R2",
        ROUTE,
        "no message id is sent when the backend has none",
        [
            (
                "    ...(messageId ? { [MESSAGE_ID_HEADER]: messageId } : {}),\n",
                "    ...{ [MESSAGE_ID_HEADER]: messageId ?? \"\" },\n",
            )
        ],
        [BFF],
        "sends no message id",
    ),
    (
        "R3",
        HOOK,
        "a finished run already in history is not replayed",
        [
            (
                "        if (hasAnswer(messagesRef.current, readMessageId(probe))) {\n"
                "          forgetRun(conversationId);\n"
                "          await probe.body.cancel();\n"
                "          return;\n"
                "        }\n",
                "        // reverted: every replayed run renders, including a finished one\n",
            )
        ],
        [REJOIN],
        "does not replay a finished run",
    ),
    (
        "R4",
        HOOK,
        "a run whose answer is not on screen is still replayed",
        [
            (
                "        if (hasAnswer(messagesRef.current, readMessageId(probe))) {\n",
                "        if (!hasAnswer(messagesRef.current, readMessageId(probe))) {\n",
            )
        ],
        [REJOIN],
        "still replays when the run finished",
    ),
    (
        "R5",
        HOOK,
        "history is loaded before the replay is joined",
        [
            (
                "        const detail = await fetchConversation(conversationId);\n",
                "        const detail = { messages: [] }; void fetchConversation;\n",
            )
        ],
        [REJOIN],
        "puts the recovered answer below the turn",
    ),
    (
        "R6",
        HOOK,
        "a conversation switch invalidates the in-flight load",
        [
            (
                "      historyOwnerRef.current = conversationId;\n",
                "      // reverted: nothing claims the transcript, so two loads race\n",
            ),
            (
                "        if (overtaken()) return;\n        updateMessages(messagesFromHistory(detail.messages));\n",
                "        updateMessages(messagesFromHistory(detail.messages));\n",
            ),
            (
                "      if (overtaken()) return;\n      const runId = readRun(conversationId);\n",
                "      const runId = readRun(conversationId);\n",
            ),
        ],
        [REJOIN],
        "discards a history load the conversation switch made stale",
    ),
    (
        "R7",
        HOOK,
        "a turn that started under a pending load is not overwritten",
        [
            (
                "        if (overtaken()) return;\n        updateMessages(messagesFromHistory(detail.messages));\n",
                "        updateMessages(messagesFromHistory(detail.messages));\n",
            ),
            (
                "      if (overtaken()) return;\n      const runId = readRun(conversationId);\n",
                "      const runId = readRun(conversationId);\n",
            ),
        ],
        [REJOIN],
        "does not let a late history load delete a turn",
    ),
    (
        "R8",
        HOOK,
        "the loader does not outdate itself by applying its own history",
        [
            (
                "        epoch = transcriptEpochRef.current;\n",
                "        // reverted: the loader's own write trips the next check\n",
            )
        ],
        [REJOIN],
        "puts the recovered answer below the turn",
    ),
    (
        "R9",
        HOOK,
        "a probe that cannot be made surfaces like any other failure",
        [
            (
                "      let messageId: string | null = null;\n\n      try {\n"
                "        // Ask first, render later.",
                "      let messageId: string | null = null;\n\n      // reverted: the probe sits outside the handler\n      {\n"
                "        // Ask first, render later.",
            ),
            (
                "        forgetRun(conversationId);\n      } catch (err) {\n",
                "        forgetRun(conversationId);\n      }\n      try {\n      } catch (err) {\n",
            ),
        ],
        [REJOIN],
        "keeps the run id when the replay could not complete",
    ),
    (
        "R10",
        HISTORY,
        "a stored tool call is read under the name the executor writes",
        [
            (
                "    const name = call.tool_name ?? call.name;\n",
                "    const name = call.name;\n",
            )
        ],
        [MAPPER],
        "reads the tool-call shape the run executor actually writes",
    ),
    (
        "R11",
        HISTORY,
        "the older tool-call spelling still renders",
        [
            (
                "    const input = call.tool_input ?? call.args;\n",
                "    const input = call.tool_input;\n",
            )
        ],
        [MAPPER],
        "still reads the older name/args spelling",
    ),
    (
        "R12",
        HISTORY,
        "a citation's own source is not overwritten by its filename",
        [
            (
                "        source: c.source ?? c.filename,\n",
                "        source: c.filename,\n",
            )
        ],
        [MAPPER],
        "keeps a citation's own source distinct from its filename",
    ),
    (
        "R13",
        HISTORY,
        "a blank row is not rendered as an empty bubble",
        [
            (
                "  return typeof row.content === \"string\" && row.content.length > 0;\n",
                "  return true;  // reverted: a blank row becomes an empty bubble\n",
            )
        ],
        [MAPPER],
        "drops a row with no content",
    ),
    (
        "R14",
        HISTORY,
        "an absent message list yields no messages instead of throwing",
        [
            (
                "  if (!Array.isArray(rows)) return [];\n",
                "  // reverted: the reload throws on a payload without `messages`\n",
            )
        ],
        [MAPPER],
        "returns nothing for absent input",
    ),
    (
        "R15",
        HISTORY,
        "an unnamed tool call is dropped rather than rendered blank",
        [
            (
                "    if (typeof name !== \"string\" || name.length === 0) continue;\n",
                "    // reverted: a nameless call renders as an empty card\n",
            )
        ],
        [MAPPER],
        "drops an unnamed tool call",
    ),
    (
        "R16",
        HISTORY,
        "a run with no persisted id is never treated as already rendered",
        [
            (
                "  if (!persistedMessageId) return false;\n",
                "  // reverted: \"no id\" reads as \"already on screen\", swallowing every live run\n",
            )
        ],
        [MAPPER],
        "is false without a persisted id",
    ),
    (
        "R17",
        HOOK,
        "a superseded load cannot clear the current load's skeleton",
        [
            (
                "        if (historyOwnerRef.current === conversationId) setIsHydrating(false);\n",
                "        setIsHydrating(false);  // reverted: any load may clear the flag\n",
            )
        ],
        [REJOIN],
        "does not let a superseded load clear the flag the current one needs",
    ),
]

REPORT = Path(r"C:\Users\ramat\AppData\Local\Temp\opencode\revert-c3-report.json")

ALL_TESTS = [REJOIN, BFF, MAPPER]


def run_tests(test_filter: str, files: list[str]) -> bool | None:
    """True = every matching test passed. False = something failed. None = unreadable."""
    cmd = [
        "node",
        "node_modules/vitest/vitest.mjs",
        "run",
        *files,
        "-t",
        test_filter,
        "--reporter=json",
        f"--outputFile={REPORT}",
    ]
    proc = subprocess.run(
        cmd,
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        data = json.loads(REPORT.read_text(encoding="utf-8"))
    except Exception:
        # A suite that failed to collect is red, but we cannot tell whether our
        # test ran at all, so report it as inconclusive rather than as a pass.
        print(f"    (could not read the json report; exit={proc.returncode})")
        print("    " + (proc.stdout or "")[-1200:])
        return None
    total = data.get("numTotalTests", 0)
    failed = data.get("numFailedTests", 0)
    passed = data.get("numPassedTests", 0)
    skipped = data.get("numPendingTests", 0)
    # `testResults` is one entry per FILE; the individual assertions live in
    # `assertionResults`. Reading a title off the file entry yields None, which
    # is how a run can look red without anyone being able to say *what* broke --
    # and "something failed" is not evidence that the right thing failed.
    titles = [
        a.get("fullName") or a.get("title")
        for f in data.get("testResults", [])
        for a in f.get("assertionResults", [])
        if a.get("status") == "failed"
    ]
    print(f"    (total={total} passed={passed} failed={failed} skipped={skipped})")
    for t in titles:
        print(f"    failed: {t}")
    if total == 0:
        return None
    return failed == 0


def main() -> int:
    # ── Preflight ───────────────────────────────────────────────────────────
    originals: dict[str, str] = {}
    problems: list[str] = []
    for rid, rel, desc, edits, _, _ in REVERTS:
        path = FRONTEND / rel
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
    print("baseline (nothing reverted) ...", flush=True)
    green = run_tests("", ALL_TESTS)
    if green is not True:
        print(f"  baseline is NOT green ({green}); restored and stopping.")
        return 2
    print("  green\n", flush=True)

    results: list[tuple[str, str, str, str]] = []
    try:
        for rid, rel, desc, edits, files, test_filter in REVERTS:
            path = FRONTEND / rel
            text = path.read_text(encoding="utf-8")
            for pristine, reverted in edits:
                assert pristine in text, rid
                text = text.replace(pristine, reverted, 1)
            path.write_text(text, encoding="utf-8")

            green = run_tests(test_filter, files)
            # Load-bearing means the revert made something RED. A revert that
            # leaves the suite green is a claim nothing is checking.
            verdict = {False: "LOAD-BEARING", True: "NOT LOAD-BEARING", None: "INCONCLUSIVE"}[
                green
            ]
            results.append((rid, desc, verdict, test_filter))
            print(f"{rid}: {verdict:16} {desc}  [{test_filter}]", flush=True)

            path.write_text(originals[rid], encoding="utf-8")
    finally:
        for rid, rel, _, _, _, _ in REVERTS:
            (FRONTEND / rel).write_text(originals[rid], encoding="utf-8")
        print("\nrestored all sources unconditionally")

    bad = [r for r in results if r[2] != "LOAD-BEARING"]
    print(f"\n{len(results) - len(bad)}/{len(results)} load-bearing")
    for rid, desc, verdict, filt in bad:
        print(f"  !! {rid} {verdict}: {desc} (filter: {filt})")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())