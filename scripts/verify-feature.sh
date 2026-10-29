#!/usr/bin/env bash
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  NEXUS AI ASSISTANT – FEATURE VERIFICATION GATE                            ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
#
# The only supported way to move a feature to "passing". It runs the feature's
# declared verification commands in order, prints an actionable repair line for
# the first one that fails, and records evidence on success.
#
#   ./scripts/verify-feature.sh F-CHAT-001
#   make verify-feature F=F-CHAT-001
#   ./scripts/verify-feature.sh --list
#
# Why a gate instead of editing JSON by hand: "passing" is a claim that runnable
# proof exists. An agent that can set it directly will set it by hand — that is
# the premature-victory failure this file exists to remove.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

FEATURE_LIST="feature_list.json"
PY="$( [ -x backend/.venv/Scripts/python.exe ] && echo backend/.venv/Scripts/python.exe || \
       { [ -x backend/.venv/bin/python ] && echo backend/.venv/bin/python || echo python; } )"

red()   { printf '\033[0;31m%s\033[0m\n' "$1"; }
green() { printf '\033[0;32m%s\033[0m\n' "$1"; }
bold()  { printf '\033[1m%s\033[0m\n' "$1"; }

[[ -f "$FEATURE_LIST" ]] || { red "feature_list.json not found in $ROOT_DIR"; exit 1; }

if [[ "${1:-}" == "--list" || -z "${1:-}" ]]; then
  "$PY" - "$FEATURE_LIST" <<'PYEOF'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"{'ID':<16} {'STATE':<12} TITLE")
for f in data["features"]:
    print(f"{f['id']:<16} {f.get('state',''):<12} {f.get('title','')}")
PYEOF
  exit 0
fi

FEATURE_ID="$1"

# ─── Run the declared verification layers ────────────────────────────────────
LAYERS_OUT="$("$PY" - "$FEATURE_LIST" "$FEATURE_ID" <<'PYEOF'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
fid = sys.argv[2]
feat = next((f for f in data["features"] if f["id"] == fid), None)
if feat is None:
    print("ERROR\tNo feature with id %s" % fid)
    raise SystemExit(2)
for i, layer in enumerate(feat.get("verification") or [], 1):
    if isinstance(layer, str):
        layer = {"label": layer, "cmd": "", "repair": ""}
    print("LAYER\t%s\t%s\t%s" % (
        layer.get("label", "layer %d" % i),
        (layer.get("cmd") or "").replace("\t", " "),
        (layer.get("repair") or "").replace("\t", " "),
    ))
PYEOF
)"
status=$?
[[ $status -eq 2 ]] && { red "$LAYERS_OUT"; exit 1; }
[[ $status -ne 0 ]] && { red "could not read $FEATURE_LIST"; exit 1; }

bold "Verifying $FEATURE_ID"
failed=0
while IFS=$'\t' read -r kind label cmd repair; do
  [[ "$kind" != "LAYER" ]] && continue
  printf '\n\033[0;36m--> %s\033[0m\n' "$label"
  if [[ -z "$cmd" ]]; then
    printf '  \033[1;33m[manual]\033[0m no command declared; verify by hand and record the evidence\n'
    continue
  fi
  # Run the layer through bash so a command with pipes/quotes behaves the same
  # locally and in CI. Output is streamed: a silent gate teaches nothing.
  if bash -c "$cmd" 2>&1 | tail -25; then
    green "  [pass] $label"
  else
    red "  [FAIL] $label"
    [[ -n "$repair" ]] && printf '  \033[1;33mrepair:\033[0m %s\n' "$repair"
    failed=$((failed + 1))
    break
  fi
done <<< "$LAYERS_OUT"

if [[ "$failed" -ne 0 ]]; then
  printf '\n'
  red "$FEATURE_ID NOT verified — state left unchanged."
  printf 'Fix the failing layer above, then re-run this command.\n'
  exit 1
fi

# ─── Record evidence and transition to passing ───────────────────────────────
COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo no-git)"
TODAY="$(date +%Y-%m-%d)"

"$PY" - "$FEATURE_LIST" "$FEATURE_ID" "$COMMIT" "$TODAY" <<'PYEOF'
import json, sys
path, fid, commit, today = sys.argv[1:5]
data = json.load(open(path, encoding="utf-8"))
feat = next(f for f in data["features"] if f["id"] == fid)
evidence = f"scripts/verify-feature.sh passed all layers on {today} at {commit}"
feat.setdefault("evidence", [])
if evidence not in feat["evidence"]:
    feat["evidence"].append(evidence)
feat["state"] = "passing"
data["last_updated"] = today
json.dump(data, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
open(path, "a", encoding="utf-8").write("\n")
print(f"\n\033[0;32m{feat['id']} -> passing\033[0m (evidence recorded in {path})")
PYEOF
