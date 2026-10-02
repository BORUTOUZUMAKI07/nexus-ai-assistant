# Nexus AI Assistant — Deployment Guide

> Verified against the source at `1f43c89` (main). Supersedes the earlier
> guide (which claimed Next.js 14, "22 SQLModel tables", `pip install -e .`,
> and Upstash Redis — all outdated). **Next.js is 16.3.4, the schema has 35
> tables, deps are managed with `uv sync`, Redis/Qdrant run locally via
> docker-compose, and object storage is Supabase Storage (hosted).**

## Production architecture

| Component | Runtime notes |
|---|---|
| **Backend API** | FastAPI via uvicorn (or gunicorn+uvicorn workers) on a Docker host. `backend/Dockerfile` is the official image. |
| **Frontend** | Next.js **16.3.4** standalone output; `frontend/Dockerfile` builds it. |
| **Database** | PostgreSQL with **pgvector** (`pgvector/pgvector:pg16` locally) — 35 SQLModel tables. Supabase/Postgres-compatible hosts work. |
| **Vector store** | Qdrant (hybrid dense+sparse). Local container `qdrant/qdrant` in compose. |
| **Redis** | Cache, rate-limiting, and Celery broker. Local `redis:7-alpine` in compose. `REDIS_URL=redis://localhost:6379/0` locally. |
| **Object storage** | Supabase Storage over its REST API (`SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` + `STORAGE_BUCKET`). No S3/R2/MinIO server — the boto3 layer was removed. |
| **Async tasks** | Celery worker + beat (file indexing, eval dispatch). |
| **Observability** | Zero-dependency Prometheus `/metrics` (in-process truth) + OTLP logs → Layer-2 collector (`docker-compose` / `render.yaml`) → New Relic; LangSmith traces; Sentry errors. |

CI/CD artifacts:
- `.github/workflows/release.yml` — on git tags: `docker buildx` builds and
  pushes `ghcr.io/<owner>/<repo>/{backend,frontend}:<tag>` + `:latest`, then
  creates a GitHub Release with a changelog.
- `.github/workflows/ci.yml` — unit+regression, security (bandit, pip-audit,
  npm audit, mypy allowlist), frontend (typegen, tsc, eslint, vitest, build),
  and optionally the live LLM-judge gates (`vars.RUN_LLM_EVAL_GATES=1`).
- `.github/workflows/nightly.yml` — nightly security scans (bandit + pip-audit),
  full backend suite (unit + integration + e2e), npm audit, frontend vitest.

## 1. Install backend dependencies

The project uses **`uv`** (not bare pip). From `backend/`:

```bash
uv sync --group dev        # prod deps + dev tools (pytest, ruff, mypy, bandit, …)
uv sync                    # prod deps only (deploy host)
```

`uv` reads `backend/pyproject.toml` + `uv.lock` for a fully pinned
environment. (There is no separate `requirements.txt` to maintain.)

## 2. Configure environment

Copy `backend/.env.example` → `backend/.env` and set values. Required for a
useful deployment:

- `DATABASE_URL` — `postgresql+asyncpg://user:pass@host:5432/nexus` (async engine)
- `REDIS_URL` — `redis://host:6379/0`
- `QDRANT_URL` / QDRANT_API_KEY
- Object storage is **Supabase Storage over its REST API** — set
  `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` and `STORAGE_BUCKET`. There are
  no S3/R2/MinIO credentials; the boto3 layer was removed and any `S3_*` key
  left in `.env` is ignored by the app.
- `SECRET_KEY` (JWT signing; `JWT_SECRET_KEY` is an alias that falls back to it)
  and `ENCRYPTION_KEY` (AES-256 key material for BYOK) — both must be fresh
  random values. Startup **aborts** in production if either is blank or still
  holds the text published in `.env.example`.
- `CORS_ORIGINS` — comma-separated browser allow-list (defaults to the two
  localhost:3000 origins)
- `MCP_API_KEY` — shared key that authenticates `/mcp` alongside Bearer JWTs and
  the `nexus_access_token` cookie (`MCP_AUTH_ENABLED=true` by default)
- Provider keys: `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`
  (BYOK users can supply their own too)
- Optional: `SENTRY_DSN`, `NEW_RELIC_ENABLED=true` +
  `NEW_RELIC_OTLP_ENDPOINT` (OTLP log export to the Layer-2 collector; requires
  `uv sync --extra observability` for the OTLP extras), `MEM0_API_KEY`
- New Relic ingestion (free tier): deploy the collector from this repo's
  `render.yaml` (or `docker compose up otel-collector` locally) and set its
  `NEW_RELIC_LICENSE_KEY` secret — the app itself never holds an NR key
- LangSmith traces: `LANGSMITH_API_KEY` + `LANGSMITH_TRACING=true` (LangGraph
  auto-instruments; free tier shows the orchestration graphs)

## 3. Run database migrations

```bash
cd backend
uv run alembic upgrade head
```

**A deployed database needs an explicit opt-in.** `upgrade` and `downgrade`
resolve `DATABASE_URL` from the environment (including `backend/.env`) and will
refuse any non-local host, exiting non-zero with the target named:

```
RuntimeError: REFUSING to run `alembic upgrade` against a non-local database.
  target:   db.example.com:5432/prod
```

That refusal is deliberate. On 2026-10-01 an `upgrade head` aimed at the local
container was pointed at the hosted database by `backend/.env` and applied a
revision to it; it turned out additive and idempotent, but nothing in the
toolchain had looked at where it was connecting. Set the flag in the deploy job:

```bash
export ALLOW_REMOTE_MIGRATIONS=1
uv run alembic upgrade head
```

Read-only commands (`history`, `current`, `heads`, `revision --autogenerate`)
and offline SQL generation (`--sql`) are **not** gated — they never change a
database. To migrate the local development database instead, start it and
override the DSN:

```bash
docker compose up -d postgres
DATABASE_URL=postgresql+asyncpg://nexus:nexus@localhost:5432/nexus_dev \
  uv run alembic upgrade head
```

**All 35 SQLModel tables** are initialized with indexes automatically
(verified: `SQLModel.metadata.tables` contains exactly 35 tables). New models
must be imported in `backend/app/infrastructure/database/engine.py` and
`backend/migrations/env.py`, and need a hand-written Alembic revision.

## 4. Start the backend

Local dev (with `.venv` from `uv sync`):

```bash
uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

Windows (event-loop factory — required so Redis/asyncpg work):

```bash
uv run uvicorn backend.app.main:app \
  --loop backend.app.infrastructure.common.event_loop:event_loop_factory \
  --host 0.0.0.0 --port 8000 --reload
```

Docker (uses `backend/Dockerfile`): pick the image from the GHCR release
(`ghcr.io/<owner>/<repo>/backend:<tag>`) or build from source.

Celery worker + beat (async indexing, eval dispatch):

```bash
uv run celery -A backend.app.worker.celery_app worker --loglevel=info
uv run celery -A backend.app.worker.celery_app beat --loglevel=info
```

Health checks: `GET /health` (liveness), `GET /health/ready` (readiness).
Metrics: `GET /metrics` (Prometheus text; bearer-guarded when `METRICS_TOKEN`
is set — the Layer-2 collector sends it when scraping).
Logs: exported over OTLP/HTTP to `NEW_RELIC_OTLP_ENDPOINT` when
`NEW_RELIC_ENABLED=true`.

## 5. Frontend

```bash
cd frontend
npm ci                     # install from package-lock.json
npm run build              # next build (standalone output)
npm run start              # NODE_ENV=production server (scripts/serve.js)
```

Point the frontend at the backend via `BACKEND_URL` (server-side env; a legacy
`NEXT_PUBLIC_API_URL` is also honored, falling back to
`http://127.0.0.1:8000`). The BFF route handlers under `frontend/src/app/api/**`
forward requests with cookies. Frontend `package.json` pins **next 16.3.4**,
react 19.2.8, typescript 5, vitest 4, playwright.

> Vercel note: the old guide suggested Vercel with `NEXT_PUBLIC_API_URL`.
> Because the browser must not expose the backend cookie/header surface, the
> recommended deployment is the provided `frontend/Dockerfile` (same-origin or
> internal networking, `BACKEND_URL` set server-side). If you use Vercel, set
> `BACKEND_URL` as a non-public env var and keep the BFF pattern.

## 6. Local infrastructure (docker-compose)

```bash
docker compose up -d       # postgres (pgvector/pg16), redis, qdrant
# Object storage is Supabase Storage (hosted) — configure SUPABASE_URL /
# SUPABASE_SERVICE_ROLE_KEY / STORAGE_BUCKET in backend/.env, no local container.
docker compose down -v     # stop + remove volumes (destructive)
```

Make targets: `make infra`, `make infra-down`, `make migrate`,
`make backend`, `make worker`, `make frontend`, `make dev`.

## 7. Migrations workflow

```bash
make migrate-auto MSG="add widgets"   # alembic revision --autogenerate
make migrate                          # alembic upgrade head
make migrate-down                     # alembic downgrade -1
make migrate-history                  # alembic history --verbose
```

## 8. SLO / alerting / runbooks

- SLO definitions: [`docs/slo.md`](slo.md)
- Prometheus rules (source of truth for paging): [`docs/alerting/alert-rules.yml`](alerting/alert-rules.yml)
- Runbooks: [`docs/runbooks/`](runbooks/README.md) — one per alert (full
  outage, high error rate, latency burn, rate-limit exhaustion, indexing
  backlog, Redis fail-open).

Validate rules: `promtool check rules docs/alerting/alert-rules.yml`.

## 9. Security checklist (matches CI `security` job)

```bash
cd backend
uv run bandit -c pyproject.toml -r app -q        # static scan (targets=app)
uv run pip-audit                                  # advisory scan on locked venv
cd ../frontend && npm audit                       # npm advisory scan
```

Notable advisories and how they're handled (see `backend/pyproject.toml`
comments):
- `ecdsa` (PYSEC-2026-1325, Minerva) — pulled by python-jose but the app only
  signs **HS256**; pip-audit is configured to ignore that single advisory.
- `ragas`, `garak`, `deepeval` were **removed** from dependencies (SSRF /
  transitive vulnerable packages / paid-model default); their functionality
  now runs through in-house deterministic heuristics + the litellm client.

## 10. Rollout & canary/shadow

- Deterministic **canary/shadow experiments** are built in: keyed user-bucket
  assignment (`EXPERIMENTS_CONFIG_PATH` YAML; unset = default variant).
- ε-greedy **bandits** reward the empirically best prompt/model variants.
- **Prompt regression gate** blocks prompt changes that regress guardrail
  behaviors (headless in CI; live LLM-judge variant behind
  `vars.RUN_LLM_EVAL_GATES`).