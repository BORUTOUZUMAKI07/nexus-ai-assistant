# Multi-stage Python 3.11 Dockerfile for Nexus AI Assistant Backend
FROM python:3.11-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --upgrade pip setuptools wheel && \
    pip install .

# Production runner stage
FROM python:3.11-slim AS runner

WORKDIR /app

# BOTH import roots must be on the path, because the code imports under two
# namespaces: `backend.app.*` (main.py, the graph, the API routers) needs /app,
# while `app.*` (rag_service and four sibling RAG/cache modules) needs
# /app/backend. Locally this is supplied by the repo root plus the editable
# install (`[tool.setuptools.packages.find] include = ["app*"]` maps `app` ->
# backend/app); here it has to be explicit. The builder copies only
# pyproject.toml, so the wheel it builds is metadata-only and provides no `app`
# package — the code arrives via `COPY . /app/backend/` in this stage. Omitting
# /app/backend boots halfway and dies with `ModuleNotFoundError: No module
# named 'app'` the first time the graph imports rag_service.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/backend

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY . /app/backend/

EXPOSE 8000

# Render (and most PaaS) inject $PORT and route to the port the container binds,
# so bind to $PORT and fall back to 8000 for a plain `docker run`. The Windows
# ProactorEventLoop workaround (`--loop ...event_loop_factory`) is a Windows-only
# requirement and is deliberately not used here — the image is Linux.
#
# --workers ${WEB_CONCURRENCY:-1}: the platform sets WEB_CONCURRENCY from the
# instance's CPU count, which is 1 on the free plan and >1 once the service is
# scaled up. Reading it means vertical scaling uses the extra cores without a
# code change, while a single process stays the default (this app's per-process
# state — checkpointer pool, in-process caches — was sized for one worker).
#
# --timeout-graceful-shutdown 20: uvicorn's default is unbounded. A rolling
# deploy that closes the SSE chat stream would then hold the old container open
# until the platform kills it, so in-flight tokens are lost; 20s lets the stream
# finish and makes replacements overlap predictably.
#
# `exec` keeps uvicorn as PID 1 so SIGTERM reaches it directly instead of being
# swallowed by the shell, which is what triggers that graceful path at all.
#
# `alembic upgrade head` before uvicorn: production skips `create_all`
# (init_db returns early when ENVIRONMENT=production), so the schema is only
# whatever the last migration run produced. Nothing applied migrations on
# deploy, so the deployed database fell behind the models and every conversation
# read failed with `column messages.cost_usd does not exist`. The run is
# non-fatal on purpose -- a migration failure must not take a serving instance
# down with it; the app then reports DATABASE_SCHEMA_OUT_OF_DATE (503) instead
# of an opaque 500, and the log line says which column is missing.
#
# ALLOW_REMOTE_MIGRATIONS=1 is the explicit opt-in the guard in
# app/core/db_target.py requires for a non-local database (that guard exists
# because a local `alembic upgrade head` once ran against production by
# accident). Setting it here scopes the permission to this container's start-up
# and to nobody's laptop.
CMD ["sh", "-c", "cd /app/backend && { ALLOW_REMOTE_MIGRATIONS=1 alembic upgrade head 2>&1 | tail -20 || echo migration_failed_continuing; }; cd /app && exec uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --timeout-graceful-shutdown 20"]
