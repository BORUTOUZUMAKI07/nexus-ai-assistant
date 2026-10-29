#!/usr/bin/env bash
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  NEXUS AI ASSISTANT – CLEAN-STATE GATE (harness clock-out phase)          ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
#
# Run this BEFORE you commit or end a session. A session is not finished until
# every check here passes, because the next session's success depends on this
# one's cleanup — that is the whole point of a clean-state protocol.
#
#   ./scripts/clean-state-check.sh          # fast gate (default)
#   FULL=1 ./scripts/clean-state-check.sh   # also runs the backend test suite
#
# It is idempotent and read-only apart from nothing: it never modifies files.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

pass() { printf '  \033[0;32m[pass]\033[0m %s\n' "$1"; }
fail() { printf '  \033[0;31m[FAIL]\033[0m %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
skip() { printf '  \033[1;33m[skip]\033[0m %s\n' "$1"; }
step() { printf '\n\033[0;36m==> %s\033[0m\n' "$1"; }

FAILURES=0
PY="$( [ -x backend/.venv/Scripts/python.exe ] && echo backend/.venv/Scripts/python.exe || \
       { [ -x backend/.venv/bin/python ] && echo backend/.venv/bin/python || echo python; } )"

printf '\033[1mClean-state check — %s\033[0m\n' "$ROOT_DIR"

# ─── 1. Session state is recorded ─────────────────────────────────────────────
step "1/6 Session state recorded in repo artifacts"
[[ -f PROGRESS.md ]] && pass "PROGRESS.md present" || fail "PROGRESS.md missing — the next session has no verified state to read"
[[ -f feature_list.json ]] && pass "feature_list.json present" || fail "feature_list.json missing — no machine-readable scope boundary"

if [[ -f PROGRESS.md ]]; then
  grep -qE '^##[[:space:]]+Current State' PROGRESS.md \
    && pass "PROGRESS.md has a '## Current State' block" \
    || fail "PROGRESS.md has no '## Current State' block (commit + verification status)"
  # A progress log whose newest entry is ancient is a log nobody trusts.
  # Tolerate markdown emphasis between the label and the date ("**Last updated:**
  # 2026-10-29" is how a human writes it); requiring digits immediately after
  # the colon rejected the natural form and failed a correct file.
  if grep -qE 'Last updated:\** *20[0-9]{2}-[0-9]{2}-[0-9]{2}' PROGRESS.md; then
    pass "PROGRESS.md carries a dated 'Last updated'"
  else
    fail "PROGRESS.md has no 'Last updated: YYYY-MM-DD' line"
  fi
fi

# ─── 2. Feature list is machine-valid ────────────────────────────────────────
step "2/6 feature_list.json is valid and honest"
if [[ -f feature_list.json ]]; then
  "$PY" - <<'PYEOF'
import json, sys
try:
    data = json.load(open("feature_list.json", encoding="utf-8"))
except Exception as exc:
    print(f"  \033[0;31m[FAIL]\033[0m feature_list.json is not valid JSON: {exc}")
    sys.exit(1)
required = {"id", "title", "state", "verification"}
states = {"not_started", "active", "blocked", "passing"}
features = data.get("features") or []
if not features:
    print("  \033[0;31m[FAIL]\033[0m feature_list.json has no features")
    sys.exit(1)
bad = [f.get("id", "<no id>") for f in features if not required <= set(f)]
if bad:
    print(f"  \033[0;31m[FAIL]\033[0m features missing required fields: {', '.join(bad)}")
    sys.exit(1)
bad = [f.get("id") for f in features if f.get("state") not in states]
if bad:
    print(f"  \033[0;31m[FAIL]\033[0m unknown state values: {', '.join(bad)}")
    sys.exit(1)
# "passing" without evidence is the exact failure mode the feature list exists
# to prevent, so it is a hard failure here rather than a warning.
no_evidence = [f["id"] for f in features if f.get("state") == "passing" and not f.get("evidence")]
if no_evidence:
    print(f"  \033[0;31m[FAIL]\033[0m marked passing with no evidence: {', '.join(no_evidence)}")
    sys.exit(1)
active = [f["id"] for f in features if f.get("state") == "active"]
if len(active) > 1:
    print(f"  \033[0;31m[FAIL]\033[0m more than one active feature (WIP=1): {', '.join(active)}")
    sys.exit(1)
print(f"  \033[0;32m[pass]\033[0m {len(features)} features, states valid, every 'passing' has evidence")
PYEOF
  [[ $? -ne 0 ]] && FAILURES=$((FAILURES + 1))
fi

# ─── 3. No conflict markers or half-applied merge state ──────────────────────
step "3/6 No unresolved merges or conflict markers"
if [[ -n "$(git diff --name-only --diff-filter=U 2>/dev/null)" ]]; then
  fail "unmerged paths present: $(git diff --name-only --diff-filter=U | tr '\n' ' ')"
else
  pass "no unmerged paths"
fi
conflicts="$(grep -rIl -E '^(<<<<<<< |>>>>>>> )' --include='*.py' --include='*.ts' --include='*.tsx' \
  --include='*.md' --include='*.yml' backend/app frontend/src docs 2>/dev/null | head -5)"
if [[ -n "$conflicts" ]]; then
  fail "conflict markers in: $(echo "$conflicts" | tr '\n' ' ')"
else
  pass "no conflict markers in source"
fi

# ─── 4. No reverted-source damage left behind ────────────────────────────────
# The revert harnesses (backend/revert_*.py, frontend/revert_c3.py) apply an
# inverse patch to real source and restore it in a finally block. A killed run
# skips the finally, so the tree can be left with a deliberate bug applied and
# the next session debugs a phantom regression.
step "4/6 No revert-harness damage left in source"
dirty="$(git status --porcelain -- backend/app frontend/src 2>/dev/null | wc -l | tr -d ' ')"
if [[ "$dirty" == "0" ]]; then
  pass "tracked source tree is clean"
else
  printf '  \033[1;33m[note]\033[0m %s modified source file(s) — confirm they are intended work, not a reverted fix:\n' "$dirty"
  git status --short -- backend/app frontend/src | sed 's/^/        /' | head -12
fi
if [[ -n "$(git diff 2>/dev/null | grep -E '^\+.*# *revert' | head -3)" ]]; then
  fail "a diff line looks like a revert marker — inspect before committing"
else
  pass "no revert markers in the working diff"
fi

# ─── 5. No debug artifacts in the change ─────────────────────────────────────
# Debug leftovers increase the cognitive load of the next session, which has to
# work out which lines were intentional. Scoped to the working diff on purpose:
# scanning the whole tree would flag every pre-existing hit and teach people to
# ignore the check.
step "5/6 No debug artifacts in the working diff"
# Two sources, because they are different failures: lines added to a tracked
# file (visible in the diff) and whole new files (invisible in the diff — a
# brand-new module is not "added lines" to git until it is staged).
new_files="$(git ls-files --others --exclude-standard -- backend/app frontend/src 2>/dev/null)"
debug_hits=""
if [[ -n "$new_files" ]]; then
  debug_hits="$(grep -nE 'console\.log\(|debugger;|breakpoint\(\)|pdb\.set_trace\(|import pdb' \
    $new_files 2>/dev/null | head -5)"
fi
diff_hits="$(git diff -U0 -- backend/app frontend/src 2>/dev/null \
  | grep -E '^\+' \
  | grep -nE 'console\.log\(|debugger;|breakpoint\(\)|pdb\.set_trace\(|import pdb' \
  | head -5)"
debug_hits="${debug_hits}${debug_hits:+$'\n'}${diff_hits}"
if [[ -n "$debug_hits" ]]; then
  fail "debug artifact(s) added in this change — remove before sharing:"
  printf '%s\n' "$debug_hits" | sed 's/^/        /'
else
  pass "no console.log / debugger / breakpoint() in added lines"
fi

# ─── 6. Verification actually runs ───────────────────────────────────────────
step "6/6 Verification pipeline"
if ( cd backend && uv run ruff check app tests migrations scripts >/dev/null 2>&1 ); then
  pass "ruff clean"
else
  fail "ruff reports problems — run: cd backend && uv run ruff check app tests migrations scripts"
fi

if [[ "${FULL:-0}" == "1" ]]; then
  echo "  running the backend suite (FULL=1), this takes minutes..."
  # Run from backend/ — never the repo root (§2 of AGENTS.md).
  if ( cd backend && uv run pytest -q >/tmp/nexus-clean-state-pytest.log 2>&1 ); then
    pass "backend suite green"
  else
    fail "backend suite failed — see /tmp/nexus-clean-state-pytest.log"
  fi
else
  skip "backend suite not run (set FULL=1 to include it)"
fi

# ─── Verdict ─────────────────────────────────────────────────────────────────
printf '\n'
if [[ "$FAILURES" -eq 0 ]]; then
  printf '\033[0;32m\033[1mCLEAN.\033[0m Safe to commit and hand off.\n'
  exit 0
fi
printf '\033[0;31m\033[1mNOT CLEAN — %s check(s) failed.\033[0m Do not hand off until these are resolved.\n' "$FAILURES"
exit 1
