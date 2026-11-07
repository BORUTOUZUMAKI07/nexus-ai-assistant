# Nexus AI Assistant — Deployment Guide

> Verified against the source at `1f43c89` (main). Supersedes the earlier
> guide (which claimed Next.js 14, "22 SQLModel tables", `pip install -e .`,
> and Upstash Redis — all outdated). **Next.js is 16.4.2, the schema has 38
> tables, deps are managed with `uv sync`, Redis/Qdrant can run locally via
> docker-compose or hosted (Upstash / Qdrant Cloud), and object storage is
> Supabase Storage (hosted).**

> Note (2026-10-16): the frontend moved to **Next.js 16.4.2** (a security
> release: the 16.3.x line carried a critical `next/og` RCE) and the schema to
> **38 tables**.

## Production architecture

| Component | Runtime notes |
|---|---|
| **Backend API** | FastAPI via uvicorn (or gunicorn+uvicorn workers) on a Docker host. `backend/Dockerfile` is the official image. |
| **Frontend** | Next.js **16.4.2** standalone output; `frontend/Dockerfile` builds it. |
| **Database** | PostgreSQL with **pgvector** (`pgvector/pgvector:pg16` locally) — 38 SQLModel tables. Supabase/Postgres-compatible hosts work. |
| **Vector store** | Qdrant (hybrid dense+sparse). Local container `qdrant/qdrant` in compose. |
| **Redis** | Cache, rate-limiting and idempotency keys. Local `redis:7-alpine` in compose. `REDIS_URL=redis://localhost:6379/0` locally. Not a job queue. |
| **Object storage** | Supabase Storage over its REST API (`SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` + `STORAGE_BUCKET`). No S3/R2/MinIO server — the boto3 layer was removed. |
| **Observability** | In-process `MetricsCollector` exported over **OTLP (logs + metrics + traces)** to the Layer-2 collector → New Relic (`docker-compose` / `render.yaml`); `/metrics` kept as a local Prometheus debug surface; LangSmith agent traces. |

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
- Optional: `NEW_RELIC_ENABLED=true` +
  `NEW_RELIC_OTLP_ENDPOINT` (OTLP log export to the Layer-2 collector; requires
  `uv sync --extra observability` for the OTLP extras), `MEM0_API_KEY`
- New Relic ingestion (free tier): deploy the collector from this repo's
  `render.yaml` (or `docker compose up otel-collector` locally) and set its
  `NEW_RELIC_LICENSE_KEY` secret — the app itself never holds an NR key
- LangSmith traces: `LANGSMITH_API_KEY` + `LANGSMITH_TRACING=true` (LangGraph
  auto-instruments; free tier shows the orchestration graphs)
- **Also set `LANGSMITH_ENDPOINT`** if you did not sign up on the default GCP-US
  deployment. LangSmith is four separate deployments (GCP US / EU / APAC / AWS
  US) with separate accounts, and a key from one returns `403` on the other
  three. Use the API host for your region, e.g.
  `https://aws.api.smith.langchain.com` — not the `smith.` UI host, which will
  not accept ingest. See `docs/observability/langsmith-trace-sample.md`.

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

**All 38 SQLModel tables** are initialized with indexes automatically
(verified: `SQLModel.metadata.tables` contains exactly 38 tables). New models
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

Health checks: `GET /health` (liveness), `GET /health/ready` (readiness).
Metrics: `GET /metrics` (Prometheus text) is a **local debug surface**. The app
exports logs, metrics and traces over OTLP/HTTP to `NEW_RELIC_OTLP_ENDPOINT`
when `NEW_RELIC_ENABLED=true`; the Layer-2 collector does **not** scrape
`/metrics`. Set `METRICS_TOKEN` anyway so a host that exposes the port cannot be
scraped unauthenticated.

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
forward requests with cookies. Frontend `package.json` pins **next 16.4.2**,
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
cd ../frontend && npm run audit:gate               # npm advisory scan + allowlist
```

Notable advisories and how they're handled (see `backend/pyproject.toml`
comments):
- `ecdsa` (PYSEC-2026-1325, Minerva) — pulled by python-jose but the app only
  signs **HS256**; pip-audit is configured to ignore that single advisory.
- `ragas`, `garak`, and a paid-model-default evaluation framework were
  **removed** from dependencies (SSRF / transitive vulnerable packages /
  paid-model default); their functionality now runs through in-house
  deterministic heuristics + the litellm client.

## 10. Rollout & canary/shadow

- Deterministic **canary/shadow experiments** are built in: keyed user-bucket
  assignment (`EXPERIMENTS_CONFIG_PATH` YAML; unset = default variant).
- ε-greedy **bandits** reward the empirically best prompt/model variants.
- **Prompt regression gate** blocks prompt changes that regress guardrail
  behaviors (headless in CI; live LLM-judge variant behind
  `vars.RUN_LLM_EVAL_GATES`).

## 11. Reference production topology (Render + Vercel)

The data services are hosted — Supabase Postgres (pgvector) + Supabase Storage,
Upstash Redis (`rediss://`), Qdrant Cloud — so only two things are deployed:

| Piece | Host | Source |
|---|---|---|
| Backend API | **Render** (web service, Docker) | `render.yaml` → `nexus-backend`, `backend/Dockerfile` |
| OTel collector (Layer 2) | **Render** (web service, Docker) | `render.yaml` → `nexus-otel-collector`, `docker/otel-collector.Dockerfile` |
| Frontend | **Vercel** | `frontend/`, server-side `BACKEND_URL` → the Render backend URL |

Steps:

1. **Collector first.** Connect the repo on Render and apply `render.yaml`; it
   prompts for every `sync: false` value. Set the collector's
   `NEW_RELIC_LICENSE_KEY` and generate an `OTLP_AUTH_USER` /
   `OTLP_AUTH_PASSWORD` pair (HTTP basic auth — the OTLP receiver is public).
2. **Backend.** Render prompts for the rest; copy values from `backend/.env`
   (gitignored). Set `NEW_RELIC_OTLP_ENDPOINT` to the collector's public URL
   (`https://nexus-otel-collector.onrender.com`) and make the backend's
   `OTLP_AUTH_*` match step 1. `ENVIRONMENT=production` aborts startup if
   `SECRET_KEY` / `ENCRYPTION_KEY` are blank or still example text — that is the
   guard working, not a bug.

   A wrong `OTLP_AUTH_*` pair is not an error the app can see: the public
   receiver answers **401** and the app keeps serving with no telemetry. Startup
   therefore logs `otlp_credentials_look_like_placeholder` when the endpoint is
   non-local and the pair is the shipped `nexus:nexus` (or shorter than 8
   characters). If that line appears, compare the two services' values — nothing
   else will tell you.

   **Free collector: accept cold-start gaps.** A Free web service sleeps after
   15 minutes without inbound traffic and takes about a minute to wake. The
   backend's own OTLP batches are the inbound traffic that wakes it, so after an
   idle period the first batch is dropped and export resumes on the next one.
   That is the accepted behaviour, not a bug to chase. Do **not** keep both
   services awake with a pinger: two always-on Free services need roughly 1,460
   of the workspace's 750 monthly Free instance hours, and Render then suspends
   every Free web service until the next month. Continuous telemetry needs a paid
   instance type on the collector (the backend can stay Free).
3. **Frontend.** On Vercel set **server-side** `BACKEND_URL` to the Render
   backend URL. The BFF route handlers proxy to it and the browser never calls
   the backend directly, so this value must **not** be `NEXT_PUBLIC_*`.
4. **Close the loop.** Once the Vercel URL exists, set the backend's
   `CORS_ORIGINS` and `APP_PUBLIC_URL` to it, and update
   `GOOGLE_OAUTH_REDIRECT_URI` / `GITHUB_OAUTH_REDIRECT_URI` to
   `https://<frontend>/api/auth/oauth/<provider>/callback` (the same URI is
   registered in each provider's console). Also set `METRICS_TOKEN`.

5. **No background worker is needed (or available).** Render's free instances
   cover web services (and Postgres/Key-Value), **not** background workers — so
   the app has no queue at all: an upload is chunked, embedded and indexed on
   the request path and answered 201 when the document is searchable. There are
   no periodic jobs to miss; webhook redelivery is available explicitly via
   `POST /webhooks/{id}/redeliver/{delivery_id}`.
   Expect the ~15-minute idle spin-down: the first request after idle
   cold-starts, and any in-flight SSE answer is bounded by Vercel Hobby's
   300-second function cap end-to-end (your runs are 10–90s; long Deep runs
   are the ones to watch).

### Free-tier ceilings, in one table

These are the numbers that only bite in production. Measured against the plans
as documented at the time of writing — re-check them before moving off free.

| Limit | Value | What it means here |
|---|---|---|
| Render free background workers | **none** | Not needed: the app has no queue. Uploads index inline (201) and there are no periodic jobs — webhook redelivery is explicit via `POST /webhooks/{id}/redeliver/{delivery_id}`. |
| Render free idle policy | spin-down after ~15 min | First request after idle cold-starts (tens of seconds). |
| Vercel Hobby function duration | **300 s** including streaming | A run must finish end to end inside it. Quick/Fast runs are 10–90 s; a very long Deep Agent run is the one at risk, and it fails as a truncated stream rather than a clean error. |
| Vercel Hobby request/response body | **4.5 MB** | This is why `MAX_UPLOAD_SIZE_MB` defaults to **4**, not 50: documents and base64 images cross the BFF, so a bigger ceiling would be enforced by the platform first, with an opaque error. Raise it only if the body stops crossing a function (or on a non-Vercel host) and mirror it in `frontend/src/lib/upload-limits.ts`. |
| Vercel Hobby licence | **non-commercial** | Commercial use needs Pro. Not a technical limit, but it is a limit. |
| Next.js 16 runtime | Node **20.9+** | Set the Node version on the Vercel project; `proxy.ts` already replaces the deprecated middleware. |
| Render free Postgres | expires after 30 days | Not used here: Postgres is Supabase, cache is Upstash, vectors are Qdrant Cloud, objects are Supabase Storage. |

**Rate limiting depends on Upstash being reachable.** If Redis is unreachable
(e.g. a TLS/IPv6 route Render cannot take), the limiter degrades instead of
failing open silently: it falls back to an in-process window and every request
increments `redis_fail_open`. Watch that metric on the deployed origin; a
sustained nonzero rate means brute-force protection is per-instance only.

Notes that are easy to get wrong:

- The backend image binds to `$PORT` (`backend/Dockerfile`), which Render
  injects. `backend/.dockerignore` is **load-bearing**: without it
  `COPY . /app/backend/` bakes `backend/.env` (Supabase service-role key, Upstash
  password, Qdrant key, JWT/encryption secrets) and the whole `.venv` into the
  image. Do not delete it.
- **A Dockerfile that builds is not a Dockerfile that runs.** `backend/Dockerfile`
  sets `PYTHONPATH=/app:/app/backend` because the code imports under two
  namespaces (`backend.app.*` plus a bare `app.*` in five `services/` files), and
  `greenlet` is a declared dependency because SQLModel's asyncio bridge needs it
  and it is otherwise only a dev-transitive package. `release.yml` only builds
  and pushes the image, so it will not catch an unbootable one. Check with
  `docker run --rm --entrypoint python <image> -c "from backend.app.main import
  app; print(len(app.openapi()['paths']))"` (expect **103**) and a
  `GET /health/ready` against a booted container.
- A container started with no env logs `env: development` and silently falls back
  to localhost services and `InMemorySaver`. An `--env-file` path that does not
  resolve yields an empty file with no error, so a wrong relative path looks like
  a broken image when it is the invocation. (CRLF line endings are fine — Docker
  handles them.)
- Render's free web services cold-start after idle; use `plan: starter` if the
  first request must be fast.
- `next.config.ts` sets `output: 'standalone'` for `frontend/Dockerfile`
  (the self-host path). Vercel runs its own build pipeline and does not consume
  that output, so a default Vercel framework preset needs no `next.config.ts`
  change.
- The hosted database is already at Alembic head. Do not run
  `alembic upgrade` against it from here — the deployment guard refuses a
  non-local host unless `ALLOW_REMOTE_MIGRATIONS=1` (see §3).