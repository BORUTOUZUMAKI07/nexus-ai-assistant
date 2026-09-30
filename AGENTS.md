# AGENTS.md — Nexus AI Assistant Repo Context

This file is the **persistent context** for AI agents and future maintainers.
Everything here was verified against the actual source at commit `1f43c89`
(main). If something here contradicts the code, **trust the code** and update
this file.

---

## 1. What this project is

Nexus AI Assistant is a full-stack **AI copilot** ("enterprise-grade AI
assistant"): a Python **FastAPI** backend implementing a **DDD modular
monolith** with a **LangGraph** multi-agent orchestrator, plus a **Next.js 16**
React frontend. It is not a toy demo — it has real auth (JWT + OAuth + 2FA +
email verification), a 35-table PostgreSQL schema, hybrid RAG on Qdrant, Celery
async jobs, mem0 memory, orgs/shares/webhooks, admin observability, and a CI
system that gates prompt regressions with live LLM judges.

**Monorepo.** Three top-level concerns:

| Path | Language/stack | Role |
|---|---|---|
| `backend/` | Python 3.11+ (CI: 3.12), FastAPI, SQLModel, LangGraph, Celery | The whole API + business logic. All Python code lives under `backend/app/`. |
| `frontend/` | TypeScript, Next.js 16.3.4 (App Router), React 19.2.8, Vitest, Playwright | Chat UI with BFF-style route handlers that proxy the backend. |
| `docs/`, `Makefile`, `docker-compose.yml`, `.github/` | Markdown, shell, YAML | Operations, infra, CI. |

The frontend talks to the backend through **Next.js route handlers**
(`frontend/src/app/api/**/route.ts`) that reverse-proxy to the FastAPI server;
the browser never talks to `localhost:8000` directly.

## 2. Verified load-bearing facts (do not "fix" these)

These were each confirmed first-hand and are intentional:

- **Windows uvicorn** must run with
  `--loop backend.app.infrastructure.common.event_loop:event_loop_factory`
  (`ProactorEventLoop` breaks Redis/asyncpg). The factory is in
  `backend/app/infrastructure/common/event_loop.py`; `backend/app/main.py`
  documents the exact flag.
- **MCP mount**: FastMCP is mounted at `/mcp` with `MCP_AUTH_ENABLED=True`
  (verified: log line `fastmcp_mounted ... auth_enabled=True path=/mcp`).
  Requests are gated by a middleware accepting: Bearer JWT, the
  `nexus_access_token` cookie, or the `x-nexus-mcp-key` shared key.
- **JWT is HS256 only.** `python-jose[cryptography]` pulls `ecdsa` (which has
  the unpatched PYSEC-2026-1325 Minerva advisory); the app only ever signs
  HS256 (`JWT_ALGORITHM` in `core/config.py`). CI's `pip-audit` ignores that
  one advisory. Do **not** add ES* algorithms without re-reviewing ecdsa.
- **OAuth is provider-based, GET-login / POST-callback**: two first-class SSO
  providers — Google (OIDC; the userinfo `email_verified` claim gates the
  login) and GitHub (OAuth2; a verified email — primary preferred — is
  re-fetched from `/user/emails` because GitHub has no trustable userinfo
  claim).
  `GET /auth/oauth/{provider}` returns the provider authorization URL (PKCE)
  and `POST /auth/oauth/{provider}/callback` accepts the code the provider
  hands back — both 404 while that provider (`google` | `github`) is
  unconfigured or unknown. Verified in `api/v1/auth.py` +
  `services/oauth_service.py` (`SSOProviderRegistry` with
  `GoogleOAuthProvider`/`GitHubOAuthProvider`); there is **no** flat
  `/auth/oauth/login` route.
- **`except Exception: pass` appears 7 times in `backend/app/` and is
  deliberate** — each is a fail-open or fail-silent seam (e.g., telemetry,
  non-critical caches). Don't blanket-remove them.
- **No mutable default arguments** anywhere in `backend/app/` (verified).
- **mypy is NOT a gate on the full tree.** It only runs over the 17-file
  allowlist in `backend/scripts/mypy_targets.txt`. The full app has ~466
  pre-existing strict-mode errors (75 files). Do not attempt a full fix.
- **No tracked `.env`.** Only `.env.example`. Real secrets live in CI
  variables / GitHub Actions secrets. Pushed history contains no real secrets
  (verified).
- **`--code-block-bg` in `src/lib/theme.ts` is intentional** (a deliberate
  comment explains it) — do not "clean up" its duplicate declaration.
- **`docs/alerting/alert-rules.yml` is the single source of truth** for
  paging. SLOs in `docs/slo.md` and runbooks in `docs/runbooks/` match it —
  each alert links to its runbook. Keep all three in sync.
- **"Books" were removed deliberately.** `AI_Engineering_Complete_Notes.md`
  and `ml_systems_design_master_notes.md` (and the audit docs that cited them)
  were deleted; **do not recreate them**. Code comments that once cited
  "§x.y of AI_Engineering_Complete_Notes" have been rewritten to describe the
  pattern inline.

## 3. Architecture (what goes where)

### Backend layout (`backend/app/`)

```
backend/app/
├── main.py                  # FastAPI app: middleware, routers, FastMCP mount, startup
├── core/                    # config (pydantic-settings), security (JWT/Fernet),
│                            # exceptions, logging, redaction, rate_limit
├── domain/                  # DDD modules — one folder per bounded context:
│   ├── user/  conversation/  file/  tool/  prompt/  org/  share/  webhook/
│   ├── plan/  artifact/  hook/  system/  experiment/  optimization/  redteam/
│   └── (each has models.py SQLModel tables, schemas.py, repository.py, service.py)
├── infrastructure/          # cross-cutting: database/engine.py (create_all),
│                            # database/session.py, database/redis.py, qdrant.py,
│                            # cache/ (response_cache, rate_limit), events/
│                            # (EventPublisher seam), common/event_loop.py
├── agents/                  # LangGraph orchestrator
│   ├── orchestrator/        # graph builder, nodes (planner/router/researcher/
│   │                        #   coder/critic/synthesizer), state, ARQ flags
│   └── subagents/           # researcher.py, coder.py, critic.py
├── services/                # cross-domain services: rag/ (embedding, retrieval,
│   │                        #   reranking, hybrid_fusion), monitoring/ (drift,
│   │                        #   slices, fairness), evaluation/ (deepeval, ragas,
│   │                        #   regression, arena, conversational, quality),
│   │                        #   experiments, bandit, confidence, audit, prompt_*
│   │                        #   optimizer, tools/ (firecrawl, e2b, elicitations),
│   │                        #   file_service, webhook_service, mem0 …
├── api/v1/                  # routers ONLY: auth, conversations, messages,
│   │                        #   audio, files, tools, prompts, settings, usage,
│   │                        #   account, orgs, shares, webhooks, admin,
│   │                        #   evaluation, plans, artifacts, metrics
│   └── deps.py              # oauth2_scheme (tokenUrl=auth/login), get_db,
│                            #   get_current_user, get_current_org_id,
│                            #   get_current_admin, require_idempotency_key,
│                            #   get_storage, get_cache, service factories
├── worker/                  # Celery: tasks.py (indexing, eval dispatch), celery_app
├── mcp/                     # FastMCP server: tools, elicitations, capabilities
├── prompt_templates/        # .txt LLM prompt templates
└── infrastructure/database/engine.py  # imports ALL domain models for create_all
```

**Conventions (verified in code, treat as law):**

- Models: `class X(SQLModel, table=True)` + `__tablename__`; UUID pk with
  `default_factory=uuid.uuid4`; timestamps as naive UTC
  (`datetime.now(UTC).replace(tzinfo=None)`); JSONB via
  `sa_column=Column(JSONB)`; **no Python enums** (str + description comments);
  **no `relationship()`** — joins are explicit queries.
- Repos: `BaseRepository[Model]` with `await self.session.exec(select(...))`,
  `.first()` / `.all()`.
- Services: structlog `logger.<level>("event_name", key=value, ...)`; transport
  deps injectable for tests.
- Routers: `from backend.app.api.deps import ...` for auth/db deps; admin routes
  use `get_current_admin` and return hand-built dicts (no `response_model` in
  `admin.py`). Every new router must be registered in `backend/app/api/v1/api.py`.
- **Tables must be imported in BOTH** `infrastructure/database/engine.py` (for
  `create_all`) and `migrations/env.py` (for autogenerate). New models need a
  hand-written Alembic revision.
- Tests are a deliberate mix: `async def test_*` (pytest-asyncio, mode AUTO)
  alongside sync `def test_*` that call an `_await(...)` helper for async code
  (see any `tests/test_*.py`); ASGI tests use `dependency_overrides` +
  `ASGITransport` (see `tests/test_observability_viewer.py`).
- No new third-party dependencies without reviewing advisories (ragas, garak,
  and deepeval were **removed** in 2026-09 due to unfixed CVEs — see comments
  in `pyproject.toml`).

### Frontend layout (`frontend/src/`)

```
frontend/src/
├── app/                     # App Router
│   ├── page.tsx             # landing page
│   ├── signin/page.tsx      # SSO sign-in
│   ├── app/page.tsx         # the main chat shell (sidebar + views)
│   ├── layout.tsx           # root layout (proxies to BFF)
│   └── api/**/route.ts      # BFF route handlers → proxy to backend
├── components/              # ChatArea, ChatInput, Sidebar, CommandPalette,
│   │                        #   ArtifactCanvas, KnowledgeView, SettingsView,
│   │                        #   UsageView, AdminView, PlanHistory, AuthModal,
│   │                        #   CitationInspector, SavedArtifacts, ui/*
├── hooks/useNexusChat.ts    # chat state machine (SSE consumption)
├── lib/                     # api.ts, auth.ts, models.ts, proxy.ts, theme.ts,
│                            #   utils.ts
├── proxy.ts                 # edge middleware: server-side session gate for /app
├── lib/proxy.ts             # shared BFF helper (cookie JWT → Authorization: Bearer)
└── test/                    # Vitest: components/, hooks/, lib/, mocks/
                            #   (MSW), route-handlers, api-parity/api-wiring
```

Frontend facts verified:

- **Next.js 16.3.4** with **React 19.2.8** (both pinned — this is not Next 13
  or 14; `docs/architecture.md` and `docs/deployment-guide.md` were corrected
  from the old "Next.js 14" claims). `frontend/AGENTS.md` says "this is NOT
  the Next.js you know" — read `node_modules/next/dist/docs/` before writing
  Next-specific code.
- **Vitest 4** (`node_modules/vitest/vitest.mjs run` on Windows because the
  `vitest` bin shim breaks), **Playwright** for e2e (`.spec.ts` in `frontend/e2e`),
  **MSW** for API mocking in tests.
- BFF route handlers proxy with credentials (cookies are forwarded). The chat
  view uses **SSE** via `fetch` + ReadableStream from
  `POST /api/v1/conversations/{id}/messages/stream`.
- **`npx next typegen` is required before `tsc --noEmit`** — `LayoutProps` /
  `PageProps` are generated globals. CI does `npx next typegen` first.
- **Design system is "Refero" — Paper (light, default) + Void (dark) + 4
  accents, never the old black-only look.** Tokens live in
  `frontend/src/app/globals.css` (`:root` Paper, `[data-theme="dark"]` Void,
  `[data-accent]` swaps), driven by `frontend/src/lib/theme.ts` and the
  pre-paint `THEME_INIT_SCRIPT`; persisted in `localStorage` (`nexus-theme` /
  `nexus-accent`), deliberately **not** from backend `UserSettings.theme`.
  Full contract + hard rules: `docs/frontend-design.md`.

### Keyboard shortcuts (verified from source — the ONLY ones that exist)

From `frontend/src/components/`:

| Shortcut | Where | Effect |
|---|---|---|
| `⌘K` / `Ctrl+K` | anywhere (`CommandPalette.tsx:68`) | toggle command palette |
| `⌘F` / `Ctrl+F` | artifact canvas (`ArtifactCanvas.tsx:92`) | focus find-in-file search |
| `⌘S` / `Ctrl+S` | artifact canvas, while editing (`ArtifactCanvas.tsx:108`) | save the in-progress edit (instead of browser save-page) |
| `Esc` | artifact canvas / slash menu (`ArtifactCanvas.tsx:97`, `ChatInput.tsx:132`) | close search, abandon edit, dismiss slash menu |
| `↑` / `↓` / `Tab` / `Enter` | chat input slash menu (`ChatInput.tsx:117-135`) | navigate/apply slash command |
| `Enter` | chat input (`ChatInput.tsx:137`, when `!shiftKey`) | send message |
| `Shift+Enter` | chat input | insert newline (send is suppressed) |

Any other shortcut (e.g. an old `Ctrl+Shift+O`/`Ctrl+Shift+B`/`Ctrl+Shift+C`
detailed-keyboard list) **does not exist in the code** — the previous
`docs/keyboard-shortcuts.md` was hallucinated and has been rewritten.

## 4. Database

**35 tables** (verified: `SQLModel.metadata.tables` at runtime). Full list:

`api_keys, artifact_versions, artifacts, audit_logs, bandit_rewards,
conversation_branches, conversation_shares, conversations, cost_logs,
evaluation_logs, file_chunks, file_metadata, files, hook_policies,
message_attachments, messages, organization_invites, organization_members,
organizations, plans, prompt_optimization_runs, prompt_templates,
prompt_versions, redteam_runs, skills, system_configs, tool_calls,
tool_permissions, tools, usage_logs, user_memories, user_settings, users,
webhook_deliveries, webhook_endpoints`

- ORM: SQLModel (SQLAlchemy under the hood); async via `asyncpg`, sync via
  `psycopg2`/`psycopg3`.
- Migrations: Alembic (`backend/migrations/`, head `f6a7b8c9d0e1` —
  7 revisions: `0001_initial_schema` → `9290fa24428d` → `a1b2c3d4e5f6` →
  `c1d2e3f4a5b6` → `d3e4f5a6b7c8` → `e5f6a7b8c9d0` → `f6a7b8c9d0e1`).
  `alembic upgrade head` before first boot.
- Dev DB: `postgres` service in docker-compose with `pgvector/pgvector:pg16`
  image (so **Postgres has the pgvector extension** for embeddings — the code
  stores some embedding vectors; do not switch to a non-pgvector image).
- Testcontainers is used for `integration`/`e2e` tests — **do not run two
  pytest sessions concurrently** (shared testcontainers collide).

## 5. Key settings & env (from `backend/app/core/config.py` + `.env.example`)

Sensible defaults exist for everything; `.env` overrides. Highlights:

- `API_V1_PREFIX=/api/v1` — used for all route mounts (single source; one
  setting, not the old duplicated `API_V1_STR`).
- `MCP_AUTH_ENABLED=True` (gate FastMCP), `MCP_API_KEY` (shared key auth).
- `ASYNC_INDEXING=false` toggles sync vs Celery 202-async file ingestion.
- `RESPONSE_CACHE_ENABLED=false` + `RESPONSE_CACHE_TTL_SECONDS=3600`
  (semantic-cost cache).
- `EXPERIMENTS_CONFIG_PATH=` → canary/shadow experiments YAML (unset =
  default variant).
- `SHARE_DEFAULT_TTL_SECONDS` — default public-share expiry.
- `JWT_ALGORITHM=HS256`, refresh tokens, `oauth2_scheme` tokenUrl=auth/login.
- `REDIS_URL=redis://localhost:6379/0` (local Redis from docker-compose).
- `SENTRY_DSN`, `NEW_RELIC_ENABLED` + `NEW_RELIC_OTLP_ENDPOINT` (optional OTLP
  log export to the Layer-2 collector — see `docker/otel-collector-config.yaml`),
  `MEM0_API_KEY`.
- The New Relic ingest key lives on the **collector**, never the app
  (`NEW_RELIC_LICENSE_KEY` in docker-compose/render.yaml; the app holds no
  vendor credentials). `/metrics` stays the in-process source of truth
  (scraped by the collector); traces are LangSmith's job; the old
  `services/observability/newrelic.py` bridge was retired to avoid
  double-counting metrics (app scrape + OTLP push).
- Bringing New Relic live is a **pending ops task** — see
  `docs/observability-deployment.md` (collector deploy → secrets → app env →
  optional `METRICS_TOKEN` guard). Code is done and on `main`; do not re-do it.
- Model routing: LiteLLM router (see `backend/app/services/llm/` + a routing
  config file); the live-evаl judge also uses LiteLLM (needs
  `OPENROUTER_API_KEY`).

Verified `.env.example` has mojibake only in the PowerShell console **render**
— the files themselves are proper UTF-8 (emoji/box-drawing chars). Do not
"fix" them.

## 6. CI / CD (all green at `1f43c89`)

`.github/workflows/`:

- **`ci.yml`** — every push/PR:
  1. `test` job: backend `pytest -m "not e2e"` (unit + behavioral-invariant;
     includes offline arena/conversational/prompt-regression heuristics).
  2. `security` job: bandit over `app/`, pip-audit over the locked venv
     (ignores the one ecdsa advisory), npm audit, **mypy over the 17-file
     allowlist**.
  3. `frontend` job: `npx next typegen` → `tsc --noEmit`, eslint, vitest,
     `next build`.
  4. **Live LLM eval gates** — job-level `if: vars.RUN_LLM_EVAL_GATES` (the
     **repository variable**, not a secret): prompt-regression, arena,
     conversational compliance, RAG faithfulness. Uses `OPENROUTER_API_KEY`
     secret. This was the CI breakage fixed: the gate previously used an
     invalid `env.…` expression in `if`.
- **`nightly.yml`** — nightly security scans (bandit + pip-audit), full backend
  suite (unit + integration + e2e), npm audit, and the frontend vitest suite.
- **`release.yml`** — on tags: build Docker image, push to GHCR.
- `concurrency: cancel-in-progress` on the same branch; 25-min `timeout-minutes`
  on the test job.

Run the same things locally:

```bash
cd backend
uv run pytest -m "not e2e"          # or: make test
uv run ruff check app tests migrations scripts
uv run bandit -c pyproject.toml -r app -q
uv run pip-audit                    # against the locked venv
uv run mypy                         # allowlist only
cd frontend
node "node_modules\vitest\vitest.mjs" run   # Windows: bin shim is broken
npx next typegen && npx tsc --noEmit
npx eslint src scripts
```

Windows shell gotchas (learned the hard way):

- PowerShell masks real exit codes — always check `$LASTEXITCODE` after tools.
- Harness scripts should run `sys.stdout.reconfigure(encoding="utf-8",
  errors="replace")` (or equivalent) or emoji/UTF-8 output looks mojibake'd.
- Use `uv run` / Make targets instead of bare `uvicorn`/`alembic` where
  possible; notebooks/scripts importing backend code should add the repo root
  to `sys.path`.
- Use `app.openapi()` (not `app.routes`) for endpoint introspection.

## 7. Testing inventory (verified counts at `1f43c89`)

- Backend: **51 unit test files** (48 in `tests/` + 3 in `tests/unit/`) +
  **11 integration** + **1 e2e** under `backend/tests/` (pytest). Fakes live
  in the single module `backend/tests/fakes.py` (e.g. `FakeSession`).
- Frontend: **25 Vitest test files** under `frontend/src/test/` + **6 Playwright
  specs** in `frontend/e2e/`.
- `walkthrough.md` once claimed "82 tests" — that is stale; the suite grew to
  211+ backend-wide. Current counts: see above.
- Backend pyproject: `addopts = -v -m 'not e2e'` — plain `pytest` skips e2e.

## 8. Common operations

| Task | Command |
|---|---|
| Start infra | `docker compose up -d` / `make infra` |
| Stop infra | `make infra-down` (removes volumes!) |
| Migrate | `make migrate`; generate: `make migrate-auto MSG="..."`; rollback: `make migrate-down` |
| Run backend | `make backend` (uvicorn :8000) |
| Run Celery worker / beat / flower | `make worker` / `make worker-beat` / `make flower` (:5555) |
| Run frontend | `make frontend` (:3000) |
| All-in-one dev | `make dev` |
| Backend tests | `make test` / `make test-unit` / `make test-integration` (needs infra) / `make test-cov` |
| Lint/format | `make lint` / `make lint-fix` / `make format` |
| Type check | `make type-check` |

## 9. Gotchas & deliberate design decisions (don't "simplify")

1. **In-memory hook registry**: hook policies are loaded at startup + on every
   admin CRUD; single-process only — fine for the current uvicorn deployment.
2. **Artifacts live in Postgres**, no blob storage — 200k char cap.
3. **HITL**: tool executions that need approval post an event the frontend
   renders as Approve/Deny cards; resolver is `POST /conversations/{id}/hitl`.
4. **SSE vs sync**: `/messages/stream` is the chat SSE endpoint; `/messages`
   returns a single aggregated response. Branching via `fork` (AG-UI alias has
   a comment citing the pattern, not a spec).
5. **Response cache + rate limiter + idempotency** are real, verified seams:
   `IdempotencyGuard` (class in `infrastructure/resilience/guards.py`, wired
   into `require_idempotency_key` in `api/deps.py`); metrics families
   `nexus_requests_total`/`nexus_errors_total`/`nexus_latency_seconds` from
   `api/v1/metrics.py` (mounted at `/metrics` with `include_in_schema=False`).
6. **Free-tier-first LLM strategy**: deepeval/garak/ragas removed; judges and
   evals use deterministic heuristics + LiteLLM free tier. Live eval gates are
   opt-in via the repo variable so contributors without model credits see green.
7. **Skills** (`backend/skills/coder.md`, `researcher.md`, `critic.md`) are
   markdown skill-guides for the subagent personas — they document behavior but
   are **not loaded at runtime** (system prompts are hardcoded constants in the
   subagent modules).
8. **`main.py` docstring and code comments** reference the real architecture —
   if you move things, keep those comments true.

## 10. Where is the source of truth for each doc

| Concern | Source of truth |
|---|---|
| API endpoints | live FastAPI `app.openapi()` (97 paths, 114 ops) — regenerate `docs/api-reference.md` from it |
| Tables | `backend/app/domain/**/models.py` (35) |
| Keyboard shortcuts | `frontend/src/components/{CommandPalette,ArtifactCanvas,ChatInput}.tsx` |
| Frontend design system | `frontend/src/app/globals.css` + `frontend/src/lib/theme.ts` + `docs/frontend-design.md` |
| Alerts/SLOs/runbooks | `docs/alerting/alert-rules.yml` ↔ `docs/slo.md` ↔ `docs/runbooks/` |
| Versions | `backend/pyproject.toml`, `frontend/package.json` |
| CI behavior | `.github/workflows/*.yml` |
| Config surface | `backend/app/core/config.py` + `backend/.env.example` |

---

_Last updated: 2026-09-29. Regenerate counts (tables/endpoints/tests) from
code rather than trusting any static number here._