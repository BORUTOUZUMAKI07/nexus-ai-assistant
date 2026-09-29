# Nexus AI Assistant — Backend

The FastAPI backend for Nexus AI Assistant: a **DDD modular monolith** with a
LangGraph agent orchestrator, hybrid RAG, Celery async jobs, and a 35-table
SQLModel/PostgreSQL schema. Python 3.11+ (CI uses 3.12).

## Stack

- **API**: FastAPI (`backend/app/main.py`), routers under `backend/app/api/v1/`
- **ORM**: SQLModel (SQLAlchemy) on PostgreSQL (pgvector image in compose)
- **Agents**: LangGraph supervisor graph (`backend/app/agents/`)
- **RAG**: Qdrant (dense + sparse), BM25, FlashRank reranking
  (`backend/app/services/rag/`)
- **Async**: Celery + Redis broker (`backend/app/worker/`)
- **MCP**: FastMCP server mounted at `/mcp` (`backend/app/mcp/`)
- **Config**: pydantic-settings (`backend/app/core/config.py`), `.env` via
  `backend/.env.example`
- **Logging**: structlog; **metrics**: zero-dependency Prometheus `/metrics`
  (in-process source of truth); structured **logs** export over OTLP/HTTP to
  the Layer-2 collector → New Relic when `NEW_RELIC_ENABLED=true`
- **Tests**: pytest (unit + integration + e2e), Testcontainers for DB-backed tests

## Quick start

```bash
# 1. Install with uv (creates .venv, syncs locked deps)
uv sync --group dev

# 2. Start local infra (Postgres, Redis, Qdrant, MinIO)
docker compose up -d        # from the repo root

# 3. Configure
cp .env.example .env        # then edit secrets/providers as needed

# 4. Migrate + run
uv run alembic upgrade head
uv run uvicorn backend.app.main:app --reload --port 8000
```

On **Windows**, the async event loop needs the project factory:

```bash
uv run uvicorn backend.app.main:app \
  --loop backend.app.infrastructure.common.event_loop:event_loop_factory \
  --reload --port 8000
```

Interactive docs: `http://localhost:8000/docs`. Health: `GET /health`,
`GET /health/ready`. MCP (auth-gated): `http://localhost:8000/mcp`.

## Project layout (backend/app)

| Path | Responsibility |
|---|---|
| `core/` | Settings, JWT/Fernet security, exceptions, logging, redaction, rate limit |
| `domain/` | One bounded context per folder (models/repos/services/schemas): user, conversation, file, tool, prompt, org, share, webhook, plan, artifact, hook, system, experiment, optimization, redteam |
| `infrastructure/` | Database engine/session/redis, Qdrant, response cache, rate-limit cache, EventPublisher seam, event loop |
| `agents/` | LangGraph orchestrator (planner → router → researcher/coder/critic → synthesizer) + subagent prompts |
| `services/` | Cross-domain logic: RAG pipeline, monitoring (drift/slices/fairness), evaluation, experiments, bandit, confidence, audit, prompt optimizer, tool adapters (Firecrawl, E2B, elicitations) |
| `api/v1/` | Routers only — wiring in `api.py`, auth deps in `deps.py` |
| `worker/` | Celery app + tasks (file indexing, eval dispatch) |
| `mcp/` | FastMCP tool server |
| `prompt_templates/` | `.txt` LLM prompt templates |

## Conventions (treat as law)

See the root [`AGENTS.md`](../AGENTS.md) for the full, verified conventions
list. The short version:

- Models: `class X(SQLModel, table=True)`, UUID pk via `default_factory`,
  naive-UTC timestamps, JSONB columns, **no Python enums, no
  `relationship()`**.
- New tables must be imported in **both** `infrastructure/database/engine.py`
  and `migrations/env.py`; add a hand-written Alembic revision.
- Repos extend `BaseRepository`; queries are plain `select(...)` so they stay
  fake-testable.
- Services log with structlog (`logger.info("event", key=value)`) and accept
  injectable transports for tests.
- New routers register in `api/v1/api.py`; admin routes use
  `get_current_admin` and hand-built dicts.
- No new third-party deps without an advisory review (see pyproject comments —
  ragas/garak/deepeval were removed over CVEs).

## Testing

```bash
uv run pytest -m "not e2e"   # unit + behavioral-invariant (default addopts skips e2e)
uv run pytest -m integration # DB-backed tests (needs compose infra/Testcontainers)
uv run pytest -m e2e         # boots a live uvicorn server
uv run pytest --cov=app --cov-report=term-missing
```

- Fakes: `backend/tests/fakes.py` (in-memory session etc.).
- ASGI tests: `dependency_overrides` + `ASGITransport` (see
  `tests/test_observability_viewer.py`).
- The CI `test` job runs the same `-m "not e2e"` selection.
- Do **not** run two pytest sessions concurrently — Testcontainers collide.

## Other commands

```bash
uv run ruff check app tests migrations scripts   # lint
uv run ruff format --check .                      # formatting
uv run bandit -c pyproject.toml -r app -q         # security scan
uv run pip-audit                                  # dependency vulnerabilities
uv run mypy                                       # curated allowlist (scripts/mypy_targets.txt)
uv run alembic revision --autogenerate -m "msg"   # new migration
uv run celery -A backend.app.worker.celery_app worker --loglevel=info
```

See the root `Makefile` (`make backend`, `make test`, `make lint`,
`make migrate`, …) for the same workflows.