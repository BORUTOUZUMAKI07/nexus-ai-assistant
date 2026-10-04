"""Revert harness for the C2 rejoin work.

Applies the inverse of each fix to the real source, runs the tests that claim to
cover it, and requires them to FAIL. A test that passes with and without the fix
is not testing the fix -- it is testing something else, and the suite is
therefore reporting a property the code does not have.

Two safety properties, both learned the hard way (AGENTS.md section 7):

  * Preflight: if a revert is already applied in the tree, refuse to run. A
    snapshot of an already-reverted tree restores the damage instead of undoing
    it, and the harness then looks like it found a regression.
  * Restore is unconditional. A harness killed mid-run leaves a real source file
    reverted; the next run then fails its own baseline.
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

# (id, file, description, pristine_text, reverted_text, vitest -t filter)
REVERTS = [
    (
        "R1",
        HOOK,
        "a finished turn clears its own run id",
        "        forgetRun(rememberedUnder);\n",
        "        void rememberedUnder; // reverted: the id is never cleared\n",
        "still holds the run id while the turn is in flight",
    ),
    (
        "R2",
        HOOK,
        "a completed rejoin clears the run id",
        "        // The replay reached the backend's end, so the run is finished.\n"
        "        forgetRun(conversationId);\n",
        "",
        "forgets the run id once the replay reaches the end",
    ),
    (
        "R3",
        HOOK,
        "a failed rejoin KEEPS the run id",
        "      } catch (err) {\n"
        '        if (err instanceof Error && err.name === "AbortError") return;\n'
        "        // The run is probably still going",
        "      } catch (err) {\n"
        '        if (err instanceof Error && err.name === "AbortError") return;\n'
        "        forgetRun(conversationId); // reverted: a failure forgets it too\n"
        "        // The run is probably still going",
        "keeps the run id when the replay could not complete",
    ),
    (
        "R4",
        HOOK,
        "a 404 run is forgotten, not surfaced as an error",
        "        if (response.status === 404) {\n"
        "          forgetRun(conversationId);\n"
        "          dropBubble();\n"
        "          return;\n"
        "        }\n",
        "",
        "forgets a run the backend no longer has",
    ),
    (
        "R5",
        HOOK,
        "a failed rejoin leaves no empty assistant bubble",
        "        dropBubble();\n        setError(\n",
        "        setError(\n",
        "keeps the run id when the replay could not complete",
    ),
    (
        "R6",
        HOOK,
        "no re-attach while a turn is already streaming",
        "    if (loadingRef.current || abortRef.current) return;\n",
        "",
        "does not re-attach while another turn still owns the conversation",
    ),
    (
        "R7",
        ROUTE,
        "Last-Event-ID is sent only when the client has a cursor",
        '        ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),\n',
        '        ...{ "Last-Event-ID": lastEventId || "0" },\n',
        "forwards Last-Event-ID only when the client has a cursor",
    ),
    (
        "R8",
        ROUTE,
        "a missing run is a distinguishable 404",
        "  if (backendRes.status === 404) {\n"
        '    return NextResponse.json({ detail: "run_not_found" }, { status: 404 });\n'
        "  }\n",
        "",
        "reports a missing run as a 404 the client can act on",
    ),
    (
        "R9",
        ROUTE,
        "a malformed rejoin target never reaches the backend",
        "  if (!UUID_RE.test(runId) || !UUID_RE.test(conversationId)) {\n"
        '    return NextResponse.json({ detail: "invalid_rejoin_target" }, { status: 400 });\n'
        "  }\n",
        "",
        "rejects a non-UUID target before it reaches the backend",
    ),
]


REPORT = Path(r"C:\Users\ramat\AppData\Local\Temp\opencode\revert-report.json")


def run_tests(test_filter: str, hook_test: str) -> bool | None:
    """True = every matching test passed. False = something failed. None = unreadable."""
    cmd = [
        "node",
        "node_modules/vitest/vitest.mjs",
        "run",
        hook_test,
        "src/test/chat-stream-rejoin.test.ts",
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
    suite_errors = [
        f.get("name") for f in data.get("testResults", []) if f.get("status") == "failed"
    ]
    print(f"    (total={total} passed={passed} failed={failed} skipped={skipped})")
    for t in titles:
        print(f"    failed: {t}")
    for s in suite_errors:
        print(f"    suite error: {s}")
    # Liveness is `total`, NOT `passed`. A load-bearing revert is *expected* to
    # leave zero passing tests, because the one test it breaks is often the only
    # one the filter selects. Reading `passed == 0` as inconclusive threw away
    # exactly the result this harness was built to produce -- which is the same
    # class of error as a test that passes for the wrong reason, made by the
    # harness itself.
    if total == 0:
        return None
    return failed == 0


def main() -> int:
    hook_test = "src/test/hooks/chat-rejoin.test.ts"

    # ── Preflight ────────────────────────────────────────────────────────────
    originals = {}
    problems = []
    for rid, rel, desc, pristine, _, _ in REVERTS:
        path = FRONTEND / rel
        text = path.read_text(encoding="utf-8")
        originals[rid] = text
        if pristine not in text:
            problems.append(f"{rid}: pristine text absent -- {desc}")
        elif rid != "R3" and text.count(pristine) != 1:
            problems.append(f"{rid}: pristine text is not unique -- {desc}")

    if problems:
        print("PREFLIGHT FAILED -- refusing to touch the tree:")
        for p in problems:
            print("  " + p)
        print("\nIf a revert below is already applied, restore it by hand first.")
        return 2

    # ── Baseline ─────────────────────────────────────────────────────────────
    print("baseline (nothing reverted) ...", flush=True)
    green = run_tests("", hook_test)
    if green is not True:
        print(f"  baseline is NOT green ({green}); restored and stopping.")
        return 2
    print("  green\n", flush=True)

    results = []
    try:
        for rid, rel, desc, pristine, reverted, test_filter in REVERTS:
            path = FRONTEND / rel
            text = path.read_text(encoding="utf-8")
            assert pristine in text, rid
            path.write_text(text.replace(pristine, reverted, 1), encoding="utf-8")

            green = run_tests(test_filter, hook_test)
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