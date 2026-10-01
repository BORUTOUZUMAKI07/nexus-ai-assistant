# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║            NEXUS AI ASSISTANT – DEVELOPER MAKEFILE                       ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
# Usage: make <target>  (e.g. make dev)

.PHONY: help infra dev backend frontend worker migrate test lint clean

# ─── Defaults ─────────────────────────────────────────────────────────────────
PYTHON     := python
UV         := uv
NPM        := npm
ALEMBIC    := $(UV) run alembic
PYTEST     := $(UV) run pytest
RUFF       := $(UV) run ruff

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ─── Infrastructure ───────────────────────────────────────────────────────────
infra: ## Start Docker services (Postgres, Redis, Qdrant)
	docker compose up -d
	@echo "Waiting for services to be healthy..."
	docker compose ps

infra-down: ## Stop and remove Docker services + volumes
	docker compose down -v

# ─── Database Migrations ──────────────────────────────────────────────────────
migrate: ## Run pending Alembic migrations
	cd backend && $(ALEMBIC) upgrade head

migrate-auto: ## Auto-generate a new migration (MSG required: make migrate-auto MSG="add users")
	cd backend && $(ALEMBIC) revision --autogenerate -m "$(MSG)"

migrate-down: ## Rollback one migration
	cd backend && $(ALEMBIC) downgrade -1

migrate-history: ## Show migration history
	cd backend && $(ALEMBIC) history --verbose

# ─── Backend ──────────────────────────────────────────────────────────────────
# uvicorn's --loop flag is REQUIRED on Windows, not a preference.
#
# uvicorn 0.36+ resolves the event-loop factory in Config.get_loop_factory
# *before* it imports the FastAPI app, so main.py's
# `asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())` never gets a
# chance to run. uvicorn then picks ProactorEventLoop, which psycopg 3 rejects
# for async I/O, and the checkpointer's first read dies with an OperationalError
# that looks like a database problem and is not one. The factory is a no-op
# branch on non-Windows platforms, so it is passed unconditionally rather than
# guarded by an OS check that would itself be wrong on WSL or Git Bash.
UVICORN_LOOP := --loop backend.app.infrastructure.common.event_loop:event_loop_factory

backend: ## Start FastAPI backend (hot reload)
	cd backend && $(UV) run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 $(UVICORN_LOOP)

worker: ## Start Celery worker
	cd backend && $(UV) run celery -A app.worker.celery_app worker --loglevel=info --concurrency=4

worker-beat: ## Start Celery beat (scheduled tasks)
	cd backend && $(UV) run celery -A app.worker.celery_app beat --loglevel=info

flower: ## Start Flower (Celery monitoring dashboard) at http://localhost:5555
	cd backend && $(UV) run celery -A app.worker.celery_app flower --port=5555

# ─── Frontend ─────────────────────────────────────────────────────────────────
frontend: ## Start Next.js frontend dev server
	cd frontend && $(NPM) run dev

frontend-install: ## Install frontend dependencies
	cd frontend && $(NPM) install

frontend-build: ## Build frontend for production
	cd frontend && $(NPM) run build

# ─── Development (all together) ───────────────────────────────────────────────
dev: infra ## Start all services + backend + frontend concurrently
	@echo "Starting backend and frontend..."
	@powershell -Command "Start-Process powershell -ArgumentList '-NoExit', '-Command', 'cd backend; uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 $(UVICORN_LOOP)'"
	@powershell -Command "Start-Process powershell -ArgumentList '-NoExit', '-Command', 'cd frontend; npm run dev'"
	@echo ""
	@echo "╔═══════════════════════════════════════════════╗"
	@echo "║  Nexus AI Assistant is starting...            ║"
	@echo "║                                               ║"
	@echo "║  Frontend:  http://localhost:3000             ║"
	@echo "║  Backend:   http://localhost:8000             ║"
	@echo "║  API Docs:  http://localhost:8000/docs        ║"
	@echo "║  Qdrant UI: http://localhost:6333/dashboard   ║"
	@echo "║  Storage:   Supabase Storage (hosted)         ║"
	@echo "╚═══════════════════════════════════════════════╝"

# ─── Testing ──────────────────────────────────────────────────────────────────
test: ## Run all backend tests
	cd backend && $(PYTEST) -v --tb=short

# 58 of the 61 unit test files sit directly in tests/, not in tests/unit/, so the
# old `pytest tests/unit` ran 3 of them and reported success. Deselect the
# integration and e2e markers instead of pointing at a directory.
test-unit: ## Run unit tests only (excludes integration/ and e2e/)
	cd backend && $(PYTEST) tests -m "not integration and not e2e" -v

test-integration: ## Run integration tests (requires running infra)
	cd backend && $(PYTEST) tests/integration -v

test-cov: ## Run tests with coverage report
	cd backend && $(PYTEST) --cov=app --cov-report=term-missing --cov-report=html

# ─── Code Quality ─────────────────────────────────────────────────────────────
lint: ## Run Ruff linter
	cd backend && $(RUFF) check .

lint-fix: ## Run Ruff linter and auto-fix
	cd backend && $(RUFF) check . --fix

format: ## Format code with Ruff formatter
	cd backend && $(RUFF) format .

# Type-check the allowlist, not the tree. The full app graph carries hundreds of
# pre-existing strict errors (see scripts/mypy_targets.txt), so `mypy app` here
# would report them forever and gate nothing — the previous version of this
# target did exactly that. This reads the same allowlist CI uses, so a local run
# and a CI run cannot disagree about what is enforced.
MYPY_TARGETS = $(shell grep -v '^\s*\#' scripts/mypy_targets.txt | sed 's/\r$$//;s|^backend/||')

type-check: ## Run mypy over the CI allowlist (scripts/mypy_targets.txt)
	cd backend && $(UV) run mypy --follow-imports=silent $(MYPY_TARGETS)

# ─── Setup ────────────────────────────────────────────────────────────────────
setup: ## First-time project setup
	@echo "Installing backend dependencies..."
	cd backend && $(UV) sync
	@echo "Installing frontend dependencies..."
	cd frontend && $(NPM) install
	@echo "Copying backend/.env.example to backend/.env..."
	@if not exist "backend\.env" (copy "backend\.env.example" "backend\.env" && echo ".env created - please fill in your API keys!")
	@echo ""
	@echo "Setup complete! Next steps:"
	@echo "  1. Edit backend/.env with your API keys"
	@echo "  2. Run: make infra     (start Docker services)"
	@echo "  3. Run: make migrate   (create database tables)"
	@echo "  4. Run: make backend   (start API server)"
	@echo "  5. Run: make frontend  (start Next.js)"

clean: ## Remove Python cache files
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
