#!/usr/bin/env bash
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  NEXUS AI ASSISTANT – MYPY ALLOWLIST RUNNER                                ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
#
# Single source of truth for the type gate. `make type-check` and the CI
# `typecheck` job both call this, so a green local run cannot disagree with CI
# about what is enforced.
#
# WHY TWO INVOCATIONS
# -------------------
# Most modules import as `app.…` and must be checked from `backend/`. A few
# (the orchestrator graph, the context compiler) import as `backend.app.…`;
# checking those from `backend/` makes mypy see one file under two module names:
#
#     error: Source file found twice under different module names:
#     "backend.app.domain.file.schemas" and "app.domain.file.schemas"
#
# which aborts the whole run. So the allowlist supports a `root:` prefix for
# entries that must be checked from the repository root, and this script runs
# each group in its own working directory.
#
# Exit: 0 = every listed module type-checks clean.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

LIST="backend/scripts/mypy_targets.txt"
[[ -f "$LIST" ]] || { echo "allowlist not found: $LIST" >&2; exit 1; }

clean() { sed 's/\r$//'; }
entries() { grep -v '^[[:space:]]*#' "$LIST" | clean | grep -v '^[[:space:]]*$'; }

mapfile -t BACKEND_TARGETS < <(entries | grep -v '^root:' | sed 's|^backend/||')
mapfile -t ROOT_TARGETS < <(entries | grep '^root:' | sed 's|^root:||')

if [[ ${#BACKEND_TARGETS[@]} -eq 0 && ${#ROOT_TARGETS[@]} -eq 0 ]]; then
  # A mis-edited allowlist must fail loudly instead of passing because nothing ran.
  echo "mypy_targets.txt yielded no targets — the allowlist is empty or unreadable" >&2
  exit 1
fi

# mypy is a backend dev dependency, so the venv interpreter can run it directly.
# `uv` is preferred (it validates the lock first) but is not guaranteed to be on
# PATH in every caller — a pytest subprocess narrows PATH, and `uv: command not
# found` used to make this gate look broken rather than unenforced.
if command -v uv >/dev/null 2>&1; then
  MYPY_BACKEND=(uv run mypy)
  MYPY_ROOT=(uv run --project backend mypy)
elif [[ -x backend/.venv/Scripts/python.exe ]]; then
  MYPY_BACKEND=("$PWD/backend/.venv/Scripts/python.exe" -m mypy)
  MYPY_ROOT=("$PWD/backend/.venv/Scripts/python.exe" -m mypy)
elif [[ -x backend/.venv/bin/python ]]; then
  MYPY_BACKEND=("$PWD/backend/.venv/bin/python" -m mypy)
  MYPY_ROOT=("$PWD/backend/.venv/bin/python" -m mypy)
else
  echo "no uv and no backend/.venv — cannot type check" >&2
  exit 1
fi

status=0

if [[ ${#BACKEND_TARGETS[@]} -gt 0 ]]; then
  echo "==> mypy (backend root): ${#BACKEND_TARGETS[@]} modules"
  ( cd backend && "${MYPY_BACKEND[@]}" --follow-imports=silent "${BACKEND_TARGETS[@]}" ) || status=1
fi

if [[ ${#ROOT_TARGETS[@]} -gt 0 ]]; then
  echo "==> mypy (repository root): ${#ROOT_TARGETS[@]} modules"
  "${MYPY_ROOT[@]}" --follow-imports=silent "${ROOT_TARGETS[@]}" || status=1
fi

if [[ "$status" -ne 0 ]]; then
  echo "type check failed — fix the reported modules or remove them from the allowlist" >&2
fi
exit "$status"
