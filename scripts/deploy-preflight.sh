#!/usr/bin/env bash
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  NEXUS AI ASSISTANT – DEPLOY PREFLIGHT                                    ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
#
# Deployment is not one step, it is a checklist with a credential at the end.
# This script runs everything that CAN be checked without an account, so the
# remaining work is exactly "paste the missing values" instead of "find out why
# production is broken at 2am".
#
#   ./scripts/deploy-preflight.sh --static     # repo artifacts only (CI-safe)
#   ./scripts/deploy-preflight.sh               # + required env vars (no secrets printed)
#   ./scripts/deploy-preflight.sh --env-file backend/.env.production
#
# It never prints a secret VALUE — only variable names and whether they are set.
# Exit 0 = the provable part is ready. Exit 1 = a named item is missing.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

STATIC_ONLY=0
ENV_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --static) STATIC_ONLY=1; shift ;;
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

pass() { printf '  \033[0;32m[ok]\033[0m   %s\n' "$1"; }
fail() { printf '  \033[0;31m[FAIL]\033[0m %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
warn() { printf '  \033[1;33m[warn]\033[0m %s\n' "$1"; }
step() { printf '\n\033[0;36m==> %s\033[0m\n' "$1"; }

FAILURES=0

# Resolve an interpreter instead of assuming `uv` is on PATH. It is absent in
# some environments that still run these scripts (a pytest subprocess inherits a
# narrowed PATH), and `uv: command not found` made the blueprint check report a
# false failure — a gate that lies is worse than no gate.
if [[ -x backend/.venv/Scripts/python.exe ]]; then
  PY=backend/.venv/Scripts/python.exe
elif [[ -x backend/.venv/bin/python ]]; then
  PY=backend/.venv/bin/python
elif command -v uv >/dev/null 2>&1; then
  PY="uv run --project backend python"
else
  PY=python
fi

# ─── 1. Render blueprint ─────────────────────────────────────────────────────
step "Render blueprint (render.yaml)"
if [[ ! -f render.yaml ]]; then
  fail "render.yaml is missing — Render has no service definition to apply"
else
  if $PY - <<'PY'
import sys, yaml
try:
    doc = yaml.safe_load(open("render.yaml", encoding="utf-8"))
except Exception as exc:
    print(f"render.yaml does not parse: {exc}")
    sys.exit(1)
services = doc.get("services") or []
if not services:
    print("render.yaml defines no services")
    sys.exit(1)
names = [s.get("name") for s in services]
if "nexus-backend" not in names:
    print(f"no nexus-backend service (found {names})")
    sys.exit(1)
for s in services:
    # Free tier is the constraint the deployment plan assumes; a paid plan is a
    # decision, not a detail to discover on the invoice.
    if s.get("plan") != "free":
        print(f"{s.get('name')} is plan={s.get('plan')!r}, not 'free'")
        sys.exit(1)
    if s.get("type") == "worker":
        print(
            "a worker service is defined; the app has no queue (uploads index "
            "inline) and Render free has no background workers"
        )
        sys.exit(1)
if not any(s.get("name") == "nexus-backend" for s in services):
    print("no nexus-backend service")
    sys.exit(1)
# There is no queue in the app any more, so the blueprint must not declare a
# background worker: a service type that needs a paid plan and that nothing
# would dispatch to.
print(f"ok: {len(services)} services, all free web services")
PY
  then pass "render.yaml parses; services are free-tier web services"
  else fail "render.yaml failed validation (see the message above)"
  fi
fi

# ─── 2. Frontend deploy config ───────────────────────────────────────────────
step "Frontend (Vercel)"
if [[ -f vercel.json ]]; then
  pass "vercel.json present"
else
  # Not a failure: Next.js on Vercel needs no vercel.json for this app shape.
  warn "no vercel.json — fine for Next.js on Vercel defaults; add one only to pin regions/builds"
fi
if [[ -f frontend/package.json ]]; then
  if $PY - <<'PY'
import json, sys
pkg = json.load(open("frontend/package.json", encoding="utf-8"))
scripts = pkg.get("scripts") or {}
if "build" not in scripts:
    print("frontend/package.json has no build script — Vercel cannot build it")
    sys.exit(1)
engines = pkg.get("engines") or {}
node = str(engines.get("node", ""))
print(f"build script present; engines.node={node or '(unset)'}")
PY
  then pass "frontend builds via npm run build"
  else fail "frontend build script missing"
  fi
fi

# ─── 3. Documentation of the deployment contract ─────────────────────────────
step "Deployment contract documented"
if grep -q "APP_PUBLIC_URL" docs/deployment-guide.md 2>/dev/null; then
  pass "deployment guide documents APP_PUBLIC_URL (share/invite links depend on it)"
else
  fail "docs/deployment-guide.md does not mention APP_PUBLIC_URL"
fi
if grep -qi "/mcp/mcp" docs/deployment-guide.md docs/api-reference.md 2>/dev/null; then
  pass "MCP JSON-RPC path (/mcp/mcp) documented where operators will look"
else
  warn "the real MCP JSON-RPC endpoint is /mcp/mcp — say so in the deployment guide"
fi

# ─── 4. Required environment (skipped in --static) ───────────────────────────
if [[ "$STATIC_ONLY" == "1" ]]; then
  step "Environment variables (skipped: --static)"
else
  step "Required environment variables"
  if [[ -n "$ENV_FILE" ]]; then
    [[ -f "$ENV_FILE" ]] || fail "env file not found: $ENV_FILE"
    # shellcheck disable=SC1090
    set -a; . "$ENV_FILE"; set +a
    pass "loaded names from $ENV_FILE (values not printed)"
  fi

  check_set() {  # check_set NAME [placeholder-substring]
    local name="$1" bad="${2:-}"
    local value="${!name:-}"
    if [[ -z "$value" ]]; then
      fail "$name is not set"
    elif [[ -n "$bad" && "$value" == *"$bad"* ]]; then
      fail "$name still contains the development placeholder"
    else
      pass "$name is set (${#value} chars)"
    fi
  }

  check_set SECRET_KEY "change-me"
  check_set ENCRYPTION_KEY "change-me"
  check_set DATABASE_URL "localhost"
  check_set CORS_ORIGINS ""
  check_set APP_PUBLIC_URL "localhost"

  # APP_PUBLIC_URL is what share links are built from; http there means every
  # shared conversation is served over plaintext.
  if [[ -n "${APP_PUBLIC_URL:-}" && "${APP_PUBLIC_URL}" != https://* ]]; then
    fail "APP_PUBLIC_URL must be https in production (share links are built from it)"
  fi

  # The checkpointer holds one long-lived connection; a transaction-mode pooler
  # (6543) closes it mid-run unless the DSN is rewritten or pinned explicitly.
  if [[ -n "${DATABASE_URL:-}" && "${DATABASE_URL}" == *":6543/"* && -z "${LANGGRAPH_CHECKPOINT_DSN:-}" ]]; then
    warn "DATABASE_URL uses the transaction-mode pooler port; set LANGGRAPH_CHECKPOINT_DSN to the session-mode DSN"
  fi

  step "Deploy credentials"
  if command -v vercel >/dev/null 2>&1; then
    pass "vercel CLI available"
  elif [[ -n "${VERCEL_TOKEN:-}" ]]; then
    pass "VERCEL_TOKEN is set"
  else
    fail "no vercel CLI and no VERCEL_TOKEN — the frontend deploy cannot be driven from here"
  fi
  if command -v render >/dev/null 2>&1; then
    pass "render CLI available"
  elif [[ -n "${RENDER_API_KEY:-}" ]]; then
    pass "RENDER_API_KEY is set"
  else
    fail "no render CLI and no RENDER_API_KEY — the backend deploy cannot be driven from here"
  fi
fi

printf '\n'
if [[ "$FAILURES" -eq 0 ]]; then
  printf '\033[0;32m\033[1mPREFLIGHT PASSED.\033[0m Everything provable locally is ready.\n'
  exit 0
fi
printf '\033[0;31m\033[1mPREFLIGHT: %s item(s) outstanding.\033[0m Each line above names what to provide.\n' "$FAILURES"
exit 1
