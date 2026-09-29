# Nexus AI Assistant

A production-grade, full-stack AI copilot: a **FastAPI + LangGraph** backend
(DDD modular monolith) paired with a **Next.js 16** frontend (App Router).
It ships agent orchestration, hybrid RAG, human-in-the-loop tool approvals,
plan mode, memory, orgs/shares, audio, MCP tool execution, admin
observability, and a CI pipeline that treats prompt regressions as test
failures.

```
┌─────────────────────┐        REST / SSE / MCP         ┌──────────────────────────┐
│ Next.js 16 Frontend │ ──────────────────────────────▶ │ FastAPI Gateway (:8000)  │
│  (BFF route proxies)│ ◀────────────────────────────── │  /api/v1 · /mcp · /docs  │
└─────────────────────┘                                 └────────────┬─────────────┘
                                                                     │ LangGraph graph
                    ┌────────────────────────────────────────────────┼───────────────┐
                    │                                                │               │
              ┌─────▼─────┐   ┌──────────────┐   ┌───────────────┐   ▼              │
              │ Postgres  │   │ Qdrant       │   │ Redis         │  Agents          │
              │ pgvector  │   │ hybrid vector│   │ cache/queue   │  (plan → route → │
              │ 35 tables │   │ (dense+sparse)│  │ + rate limit  │   research/code →│
              └───────────┘   └──────────────┘   └───────────────┘   synthesize)    │
                                                                      └──────────────┘
```

## Repository layout

| Path | What it is |
|---|---|
| `backend/` | FastAPI app — domain/services/API layers, LangGraph orchestrator, RAG, Celery workers, Alembic migrations. `uv sync`-managed. |
| `frontend/` | Next.js 16.3.4 App Router app — chat UI, plan review, artifact canvas, admin + settings views. BFF route handlers proxy the API. |
| `docs/` | Architecture, API reference, deployment guide, SLOs, alerting rules, runbooks, user guide, keyboard shortcuts. |
| `.github/workflows/` | CI (`ci.yml`), nightly security + full test suites (`nightly.yml`), release (`release.yml`). |
| `Makefile` | Infra / migrate / run / test targets (see below). |
| `docker-compose.yml` | Local infra: Postgres (pgvector), Redis, Qdrant, MinIO. |

## Quick start

Prerequisites: Python 3.11+ (CI uses 3.12), Node 22+, Docker.

```bash
# 1. Start local infrastructure
docker compose up -d

# 2. Backend
cd backend
uv sync --group dev
cp .env.example .env          # adjust secrets as needed
uv run alembic upgrade head
uv run uvicorn backend.app.main:app --reload --port 8000

# 3. Frontend (second terminal)
cd frontend
npm install
npm run dev                   # http://localhost:3000
```

Everything can also be driven from the repo root via Make:

```bash
make setup        # one-time install (backend deps + frontend deps)
make infra        # docker compose up -d
make migrate      # alembic upgrade head
make backend      # uvicorn hot-reload on :8000
make frontend     # next dev on :3000
make dev          # infra + backend + frontend
make test         # backend pytest (unit + integration, not e2e)
make lint         # ruff
make type-check   # mypy over the curated allowlist
```

Local endpoints once running:

- Backend API: `http://localhost:8000/api/v1` — interactive docs at `http://localhost:8000/docs`
- FastMCP endpoint (auth-gated): `http://localhost:8000/mcp`
- Health: `GET /health`, `GET /health/ready`
- Prometheus metrics: `GET /metrics` (not in the OpenAPI schema)
- Frontend: `http://localhost:3000`

> **Windows note:** run uvicorn with the async event-loop factory flag to avoid
> `ProactorEventLoop` issues:
> `uv run uvicorn backend.app.main:app --loop backend.app.infrastructure.common.event_loop:event_loop_factory --reload --port 8000`

## What the app does

- **Multi-agent chat** — a LangGraph supervisor graph with planner, router,
  researcher, coder, critic, and synthesizer nodes. Responses stream over SSE
  (`POST /api/v1/conversations/{id}/messages/stream`), with thinking/CoT
  blocks, citations, tool events, and human-in-the-loop interrupts.
- **Plan mode** — ask for a plan first; approve/reject it before execution.
  Plans are persisted per conversation (`/plans/{id}/approve|reject`).
- **Hybrid RAG** — upload PDF/Markdown/text/JSON; files are chunked, embedded
  (dense + sparse), indexed into Qdrant, and retrieved with
  reciprocal-rank fusion + FlashRank reranking and sentence-level salience
  filtering. Citations are numbered and clickable.
- **Tools & HITL** — web search (Firecrawl/DuckDuckGo), E2B sandboxed code
  execution, file IO, and MCP elicitations. Sensitive tools require approval;
  approvals/history live in the chat feed and `POST /tools/approval`.
- **Memory & BYOK** — mem0 long-term memory endpoints (`/settings/memories`)
  and per-user provider keys (Groq/OpenRouter/OpenAI) encrypted at rest with
  Fernet (`/settings/keys`).
- **Artifacts** — server-side versioned documents produced by the agent,
  viewable/editable in the frontend artifact canvas.
- **Orgs, sharing, webhooks, audio** — organization membership + invites,
  public conversation shares, outbound webhook endpoints with retryable
  deliveries, and TTS (edge-tts) + transcription.
- **Admin & observability** — user management, hook policies, drift/fairness/
  bandit/slice monitoring, optimizer runs, EU-AI-Act audit reports, red-team
  probes, and eval job triggers — all behind `get_current_admin`.

## API surface (verified from live OpenAPI)

**97 paths · 114 operations**, all under `/api/v1` unless noted. Domain areas:

- `auth` — register, login, refresh, logout, me, email verification, password
  reset, 2FA (TOTP), OAuth (GitHub/Google) callback flow
- `conversations` — CRUD, search, fork, plan, HITL, sync + SSE streaming,
  per-message feedback
- `files` — upload (sync or 202-async via Celery + `Location` header),
  chunk listing, RAG query, index-status
- `tools` — list, execute, approval, elicitations (+ respond)
- `prompts` — templates CRUD, skills list
- `settings` — profile settings, provider keys, memories
- `usage` — per-user summary + evaluation history
- `account` — profile + data export, account deletion
- `orgs` — org CRUD, invites, members, usage summary
- `shares` — public conversation sharing
- `webhooks` — endpoint CRUD, deliveries, redeliver
- `plans` / `artifacts` / `audio` / `admin` — plan approve/reject, artifact
  CRUD + versions, speech/transcribe, admin surface (users, hooks, monitoring,
  optimization, evaluation)

Full endpoint tables live in [`docs/api-reference.md`](docs/api-reference.md).

## Testing & quality gates

| Gate | Command | Focus |
|---|---|---|
| Backend unit + regression | `cd backend && uv run pytest -m "not e2e"` | 51 unit test files (48 in `tests/` + 3 in `tests/unit/`) + 11 integration files; offline arena/conversational/prompt-regression heuristics always run |
| Backend e2e | `uv run pytest -m e2e` | boots a live uvicorn server (requires running infra) |
| Frontend unit | `cd frontend && npm test` | Vitest 4 — 25 test files across components, hooks, lib, BFF routes, mock-service-worker |
| Frontend e2e | `npm run test:e2e` | Playwright — 6 specs |
| Type check | `npx tsc --noEmit` (+ `npx next typegen`) | strictly typed frontend |
| Python lint | `ruff check app tests migrations scripts` | E/F/I/N/W |
| Format | `ruff format --check .` | ruff formatter, 120-char lines |
| Mypy | `uv run mypy` | **curated allowlist only** — `backend/scripts/mypy_targets.txt` (17 files); the full graph still carries pre-existing strict-mode debt |
| Security | `bandit -r app`, `pip-audit`, `npm audit` | runs in CI job `security` |

See [`.github/workflows/ci.yml`](.github/workflows/ci.yml) for the exact CI
matrix: `test` (unit suite + regression gate + ruff lint), `typecheck`
(mypy over the curated allowlist), `security` (bandit, pip-audit, npm audit),
`frontend` (typegen + tsc + eslint + vitest + next build). A separate
credential-gated job runs **live LLM-judge eval gates** (prompt-regression,
arena, conversational compliance, RAG faithfulness) only when the
`RUN_LLM_EVAL_GATES` repository **variable** is `1` — the judge uses LiteLLM
routing, so it needs `OPENROUTER_API_KEY`. Nightly (`nightly.yml`) re-runs the
security scans, the full backend suite (unit + integration + e2e), npm audit,
and the frontend vitest suite; `release.yml` builds and pushes GHCR images
(`backend` + `frontend`) on tags.

## Documentation

| Doc | Contents |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | System architecture, modules, data flow, reliability seams |
| [`docs/api-reference.md`](docs/api-reference.md) | Full endpoint reference + FastMCP |
| [`docs/deployment-guide.md`](docs/deployment-guide.md) | Production deployment, Dockerfiles, migrations |
| [`docs/user-guide.md`](docs/user-guide.md) | End-user feature walkthrough |
| [`docs/keyboard-shortcuts.md`](docs/keyboard-shortcuts.md) | Verified in-app shortcuts |
| [`docs/slo.md`](docs/slo.md) | SLO/SLI definitions and error-budget policy |
| [`docs/alerting/alert-rules.yml`](docs/alerting/alert-rules.yml) | Prometheus alerting rules (source of truth for pages) |
| [`docs/runbooks/`](docs/runbooks/) | Runbooks for each paging alert |
| [`AGENTS.md`](AGENTS.md) | Repo context for AI agents / future maintainers |
| [`backend/README.md`](backend/README.md) | Backend-specific developer guide |
| [`frontend/README.md`](frontend/README.md) | Frontend-specific developer guide |

## License

Private project — all rights reserved. See `backend/pyproject.toml` for
authorship metadata.