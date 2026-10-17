"""Revert harness for §9.41 (Fast mode).

For each fix, apply its inverse to the real source, run the covering test(s),
and require them to FAIL -- then restore unconditionally. A test that passes
with and without the fix is not testing the fix.

Each revert is a list of (file, old, new) edits so a fix whose inverse is a
*reordering* (R4: moving the fast return above the memory load) can be
expressed, not just a deletion.

Run: `cd backend && uv run python revert_fast_mode.py`
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
GRAPH = BACKEND / "app" / "agents" / "orchestrator" / "graph.py"
NODES = BACKEND / "app" / "agents" / "orchestrator" / "nodes.py"

FAST_ROUTE = '    if state.get("mode") == "fast":\n        return "synthesizer"\n'
FAST_PLANNER_BLOCK = (
    "    # Fast mode is a single direct answer: `route_after_planner` sends it straight\n"
    "    # to the synthesizer, so planning (and with it the orchestrator, subagents,\n"
    "    # tools and retrieval) is dropped. The memory block loaded above is kept, so\n"
    "    # fast mode still personalises; only the multi-step machinery is skipped.\n"
    '    if state.get("mode") == "fast":\n'
    '        return {"plan": None, "current_step": 0, "user_memories": [memory_block] if memory_block else []}\n'
    "\n"
)
LAST_USER_LINE = "    last_user_msg = _extract_text_content(last_user_raw)\n"

# (label, [(file, old, new), ...], -k filter)
REVERTS = [
    (
        "R1 route_after_planner fast branch",
        [(GRAPH, FAST_ROUTE, "")],
        "routing or fast or plan",
    ),
    (
        "R2 planner fast early-return",
        [(NODES, FAST_PLANNER_BLOCK, "")],
        "planner",
    ),
    (
        "R3 planner->synthesizer edge entry",
        [(GRAPH, '            "synthesizer": "synthesizer",\n', "")],
        "wired",
    ),
    (
        "R4 fast return moved above the memory load",
        [
            (NODES, FAST_PLANNER_BLOCK, ""),
            (
                NODES,
                LAST_USER_LINE,
                LAST_USER_LINE
                + '\n    if state.get("mode") == "fast":\n'
                + '        return {"plan": None, "current_step": 0, "user_memories": []}\n',
            ),
        ],
        "memor",
    ),
]


def _load(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _save(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def run_tests(expr: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_fast_mode.py", "-k", expr, "-q"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    snapshots = {GRAPH: _load(GRAPH), NODES: _load(NODES)}

    # Preflight: refuse to start on an already-reverted tree.
    for label, edits, _k in REVERTS:
        for path, old, _new in edits:
            if old not in snapshots[path]:
                print(f"PREFLIGHT FAIL: {label}: expected text not found in {path.name}. "
                      f"Tree may already be reverted; refusing to start.")
                return 2

    results: list[tuple[str, bool, str]] = []
    try:
        for label, edits, expr in REVERTS:
            for path, old, new in edits:
                text = _load(path)
                _save(path, text.replace(old, new, 1))
            try:
                rc, out = run_tests(expr)
            finally:
                for path in snapshots:
                    _save(path, snapshots[path])
            summary = [ln for ln in out.splitlines() if "failed" in ln or "passed" in ln]
            ran = any("passed" in ln or "failed" in ln for ln in summary)
            results.append((label, rc != 0 and ran, "\n".join(summary[-2:])))
    finally:
        for path, text in snapshots.items():
            if _load(path) != text:
                _save(path, text)
        for path, text in snapshots.items():
            assert _load(path) == text, f"failed to restore {path}"

    print("\n=== Revert harness results (want FAIL=load-bearing) ===")
    all_ok = True
    for label, failed, tail in results:
        status = "LOAD-BEARING" if failed else "NOT LOAD-BEARING (!)"
        all_ok = all_ok and failed
        print(f"\n[{status}] {label}\n{tail}")
    print(f"\n{'ALL LOAD-BEARING' if all_ok else 'SOME NOT LOAD-BEARING'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
