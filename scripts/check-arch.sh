#!/usr/bin/env bash
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  NEXUS AI ASSISTANT – ARCHITECTURAL RULE RUNNER                            ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
#
# Runs every rule in .harness/arch-rules.json and prints WHAT / WHY / FIX for
# each violation.
#
#   ./scripts/check-arch.sh              # all rules
#   ./scripts/check-arch.sh --list       # rule ids and their severity
#   ./scripts/check-arch.sh browser-*    # only matching rule ids
#
# Why a failure must be a repair instruction: a message like "direct fs access
# in renderer" tells an agent something is wrong without telling it what to do,
# so the next attempt is a guess. Naming the file, the reason and the fix turns
# the failure into the edit. This is the "agent-oriented error message" rule —
# see docs/harness/gotchas.md and the promotion rule in AGENTS.md §2.
#
# Exit 0 = every rule holds. Exit 1 = at least one violation (or a broken rule
# file, which is also a failure: a registry that cannot be read silently gates
# nothing).

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RULES=".harness/arch-rules.json"
[[ -f "$RULES" ]] || { echo "arch rules not found: $RULES" >&2; exit 1; }

PY="$( [ -x backend/.venv/Scripts/python.exe ] && echo backend/.venv/Scripts/python.exe || \
       { [ -x backend/.venv/bin/python ] && echo backend/.venv/bin/python || echo python; } )"

exec "$PY" - "$RULES" "$@" <<'PYEOF'
from __future__ import annotations

import fnmatch
import json
import re
import sys
from pathlib import Path

rules_path = sys.argv[1]
args = sys.argv[2:]

try:
    registry = json.loads(Path(rules_path).read_text(encoding="utf-8"))
except Exception as exc:  # noqa: BLE001 - a broken registry must fail loudly
    print(f"cannot parse {rules_path}: {exc}", file=sys.stderr)
    raise SystemExit(1)

rules = registry.get("rules") or []
if not rules:
    print(f"{rules_path} defines no rules — an empty registry gates nothing", file=sys.stderr)
    raise SystemExit(1)

if "--list" in args:
    for r in rules:
        print(f"{r['id']:<40} {r.get('severity', 'error')}")
    raise SystemExit(0)

filters = [a for a in args if not a.startswith("-")]
if filters:
    rules = [r for r in rules if any(fnmatch.fnmatch(r["id"], f) for f in filters)]
    if not rules:
        print(f"no rule matches {filters}", file=sys.stderr)
        raise SystemExit(1)


def iter_files(rule: dict):
    for base in rule.get("paths", []):
        root = Path(base)
        if not root.exists():
            continue
        pattern = rule.get("glob", "*")
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.as_posix()
            if not fnmatch.fnmatch(path.name, pattern):
                continue
            if any(fnmatch.fnmatch(rel, exc) for exc in rule.get("exclude_paths", [])):
                continue
            yield path


violations = 0
checked = 0

for rule in rules:
    rid = rule["id"]
    regex = re.compile(rule["pattern"])
    hits: list[tuple[str, int, str]] = []
    for path in iter_files(rule):
        checked += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                hits.append((path.as_posix(), lineno, line.strip()))

    if not hits:
        continue

    violations += len(hits)
    print(f"\n\033[0;31m[VIOLATION]\033[0m {rid}  ({len(hits)} occurrence(s))")
    for where, lineno, snippet in hits[:5]:
        print(f"    {where}:{lineno}")
        print(f"      {snippet[:120]}")
    if len(hits) > 5:
        print(f"    ... and {len(hits) - 5} more")
    print(f"\n  WHAT: {rule.get('what', '(not documented)')}")
    print(f"  WHY:  {rule.get('why', '(not documented)')}")
    print(f"  FIX:  {rule.get('fix', '(not documented)')}")

print()
if violations:
    print(
        f"\033[0;31m{len(rules)} rule(s), {violations} violation(s) in {checked} files.\033[0m"
    )
    print("Each violation above names its own fix — apply it, then re-run this command.")
    raise SystemExit(1)

print(f"\033[0;32mAll {len(rules)} architectural rules hold\033[0m ({checked} files scanned).")
PYEOF
