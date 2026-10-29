#!/usr/bin/env bash
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  NEXUS AI ASSISTANT – STANDARD STARTUP PATH (harness init phase)          ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
#
# Why this file exists: an agent session must verify the environment is healthy
# BEFORE it starts changing code, otherwise a broken baseline is indistinguishable
# from a regression it just caused. Run this first, every session.
#
#   ./init.sh                      # infra up + deps + migrate + print commands
#   NEXUS_SKIP_INFRA=1 ./init.sh   # assume containers are already up
#   NEXUS_SKIP_DEPS=1  ./init.sh   # skip the dependency sync check
#   NEXUS_SKIP_MIGRATE=1 ./init.sh # skip alembic (read-only / investigation work)
#   RUN_START_COMMAND=1 ./init.sh  # launch backend on :8000 after checks pass
#
# Exit codes: 0 = environment healthy, 1 = a prerequisite is missing or unhealthy.
# A non-zero exit here means FIX THIS FIRST — do not stack feature work on it.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

BACKEND_PORT="${NEXUS_BACKEND_PORT:-8000}"
ok()   { printf '  \033[0;32m[ok]\033[0m   %s\n' "$1"; }
warn() { printf '  \033[1;33m[warn]\033[0m %s\n' "$1"; }
die()  { printf '  \033[0;31m[fail]\033[0m %s\n' "$1" >&2; exit 1; }
step() { printf '\n\033[0;36m==> %s\033[0m\n' "$1"; }

printf '\033[1mNexus init — %s\033[0m\n' "$ROOT_DIR"

# ─── 1. Toolchain ─────────────────────────────────────────────────────────────
step "Toolchain"
command -v uv  >/dev/null 2>&1 && ok "uv $(uv --version 2>/dev/null | awk '{print $2}')" \
  || die "uv not found — install: https://docs.astral.sh/uv/"
command -v npm >/dev/null 2>&1 && ok "npm $(npm --version)" \
  || die "npm not found — install Node 20.9+ (Next 16 requirement)"
command -v docker >/dev/null 2>&1 && ok "docker present" \
  || die "docker not found — needed for Postgres/Redis/Qdrant"

# ─── 2. Infrastructure ────────────────────────────────────────────────────────
if [[ "${NEXUS_SKIP_INFRA:-0}" == "1" ]]; then
  step "Infrastructure (skipped: NEXUS_SKIP_INFRA=1)"
else
  step "Infrastructure"
  # The Docker daemon being down is the single most common cause of a "broken"
  # baseline: 11 integration test files error at fixture setup with
  # DockerException and the run looks like a catastrophic regression.
  docker info >/dev/null 2>&1 || die "Docker daemon is not running — start Docker Desktop, then re-run ./init.sh"

  docker compose up -d --wait
  for svc in postgres redis qdrant; do
    state="$(docker inspect -f '{{.State.Health.Status}}' "nexus_${svc}" 2>/dev/null || echo missing)"
    case "$state" in
      healthy) ok "$svc healthy" ;;
      missing) warn "$svc container not found (check 'docker compose ps')" ;;
      *)       warn "$svc health = $state" ;;
    esac
  done

  # Resolve the PUBLISHED port instead of hardcoding it. docker-compose.override.yml
  # remaps postgres (a native postgres.exe can hold 5432), so a hardcoded 5432 is
  # only correct on machines that have no override file.
  pg_port="$(docker compose port postgres 5432 2>/dev/null | awk -F: '{print $NF}')"
  [[ -n "$pg_port" ]] && ok "postgres published on host port $pg_port" \
                      || warn "could not resolve the published postgres port"
fi

# ─── 3. Dependencies ──────────────────────────────────────────────────────────
if [[ "${NEXUS_SKIP_DEPS:-0}" == "1" ]]; then
  step "Dependencies (skipped: NEXUS_SKIP_DEPS=1)"
else
  step "Dependencies"
  if [[ ! -x backend/.venv/Scripts/python.exe && ! -x backend/.venv/bin/python ]]; then
    ( cd backend && uv sync )
    ok "backend venv created (uv sync)"
  else
    ok "backend venv present (run 'cd backend && uv sync' if imports fail)"
  fi
  [[ -d frontend/node_modules ]] && ok "frontend node_modules present" \
    || { ( cd frontend && npm ci ); ok "frontend deps installed (npm ci)"; }
fi

# ─── 4. Database migrations ───────────────────────────────────────────────────
# Alembic MUST run from backend/ (alembic.ini's script_location and
# prepend_sys_path are resolved against the CWD, not the ini file).
#
# migrations/env.py refuses a non-local host unless ALLOW_REMOTE_MIGRATIONS=1,
# so this step cannot accidentally migrate the hosted Supabase database — the
# guard is in the toolchain, and this script does not weaken it.
if [[ "${NEXUS_SKIP_MIGRATE:-0}" == "1" ]]; then
  step "Migrations (skipped: NEXUS_SKIP_MIGRATE=1)"
else
  step "Migrations"
  if [[ "${NEXUS_SKIP_INFRA:-0}" != "1" ]]; then
    ( cd backend && uv run alembic upgrade head )
    ok "alembic upgrade head applied"
  else
    warn "migrations skipped because infra was skipped"
  fi
fi

# ─── 5. Standard commands ─────────────────────────────────────────────────────
step "Standard commands"
printf '  verify (must pass before claiming done):  make check\n'
printf '  full backend suite:                      make test\n'
printf '  clean-state gate (clock-out):            make clean-check\n'
printf '  backend:  cd backend && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port %s \\\n' "$BACKEND_PORT"
printf '              --loop backend.app.infrastructure.common.event_loop:event_loop_factory\n'
printf '  frontend: cd frontend && npm run dev\n'
printf '  both:     make dev\n'

printf '\n\033[0;32mEnvironment looks healthy.\033[0m Read PROGRESS.md for the last verified state, then pick ONE feature from feature_list.json.\n'

if [[ "${RUN_START_COMMAND:-0}" == "1" ]]; then
  step "Starting backend"
  exec env PYTHONPATH="$ROOT_DIR" uv run --project backend uvicorn backend.app.main:app \
    --reload --host 0.0.0.0 --port "$BACKEND_PORT" \
    --loop backend.app.infrastructure.common.event_loop:event_loop_factory
fi
