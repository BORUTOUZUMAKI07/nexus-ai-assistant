# AGENTS.md — Nexus AI Assistant Repo Context

This file is the **persistent context** for AI agents and future maintainers.
Everything here was verified against the actual source through the D3 commit
(Batches A–D) plus the checkpointer `isinstance` fix. If something here
contradicts the code, **trust the code** and update this file.

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
- **`GET /auth/oauth/providers` is declared BEFORE `/oauth/{provider}`, and it
  has to be.** It reports which IdPs this deployment can actually complete a
  login with (`SSOProviderRegistry.describe_providers()`), so the sign-in page
  can render exactly those buttons instead of a hardcoded pair. FastAPI matches
  in registration order, so a literal path registered *after* the catch-all is
  captured as a provider named `providers` and answered with the "unconfigured"
  404 — indistinguishable from SSO being off, with no test anywhere red. The
  same ordering rule applies twice more on the way out: Next's
  `app/api/auth/oauth/providers/route.ts` sits beside `[provider]/route.ts`, and
  the MSW mock at `src/test/mocks/handlers.ts` sits above the `:provider`
  handler. `:provider` matches the string `"providers"`, so all three would be
  shadowed by the wrong order. The endpoint is **unauthenticated** by
  necessity — the sign-in page has no session yet, and that is the only moment
  it needs this — and returns names plus a boolean, nothing else.
- **"SSO" in this codebase is a name for OAuth, not a second auth system.**
  Single sign-on is the *outcome* ("you were already signed in to Google, so
  you are signed in here"); OAuth 2.0 / OIDC is the *protocol* that produces
  it. Google and GitHub OAuth **are** SSO. So `SSOProviderRegistry` and
  `SSO_LABELS` in `services/oauth_service.py` and
  `frontend/src/app/signin/page.tsx` describe the same two providers as
  `/auth/oauth/google` and `/auth/oauth/github` — there is no separate SSO
  mechanism, no SAML, no third provider. The naming is redundant, not wrong;
  **do not "fix" it by introducing a second auth path**, and do not rename it
  casually — the registry name is referenced from tests and the frontend label
  tables.
- **8 `except` handlers in `backend/app/` have `pass` as their entire body,
  and are deliberate** — each is a fail-open or fail-silent seam (e.g.,
  telemetry, non-critical caches). Don't blanket-remove them. A ~64 more are
  "silent" only in the sense that their body is a docstring plus a `return` /
  `assign`; those are real logic, not swallows. **Prefer a documented body
  over a bare `pass` in new code** — the new Batch C/D modules deliberately use
  a docstring-then-`return` form so the intent is legible and `bandit` (a CI
  gate) stays at zero. Verify any count you rely on with
  `ast.walk(tree)`, not a regex: on CRLF checkouts a `\s*\n\s*` pattern
  matches nothing and you will conclude the seams were removed.
- **No mutable default arguments** anywhere in `backend/app/` (verified).
- **mypy is NOT a gate on the full tree.** It only runs over the 18-file
  allowlist in `backend/scripts/mypy_targets.txt`, and the command needs those
  paths passed explicitly (`--follow-imports=silent`, `backend/` prefix
  stripped) — bare `uv run mypy` has no target and exits 2. The full app has
  ~466 pre-existing strict-mode errors (75 files). Do not attempt a full fix.
  New modules belong on the allowlist: check one with
  `uv run mypy --follow-imports=silent <module>` and append it if clean.
- **`ResilientPostgresSaver` must subclass `BaseCheckpointSaver`, and did
  not.** `StateGraph.compile()` calls `ensure_valid_checkpointer`, which is an
  `isinstance` check
  (`.venv/.../langgraph/types.py:109`); `lifespan_graph` catches the resulting
  `TypeError` and falls back to `InMemorySaver`. So the wrapper shipped as a
  duck-typed class with every right method, was *discarded at compile time*, and
  every one of its 18 tests passed — they all exercise the wrapper directly and
  never ask LangGraph whether it accepts it. Production ran with no
  persistence while the reconnect logic never executed once. The class now
  subclasses the base and `FORWARDED_METHODS` is the base's full async surface
  (anything not forwarded inherits a `NotImplementedError` stub).
- **Run `pytest` from `backend/`, never the repo root.** The Batch A–D test
  files open source files by repo-root-relative path (`app/services/...`), so
  from the repo root every one of them raises `FileNotFoundError`: 131 spurious
  failures and 124 errors that look like a catastrophic regression and are
  entirely a CWD mistake. `uv run --project backend pytest` from the root hits
  this; `cd backend && uv run pytest` is the documented run.
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
- **The injection guardrail canonicalizes before it matches, and the fold is
  lossy on purpose.** `guardrail_service.canonicalize_for_detection()` folds
  NFKC, invisible characters, Cyrillic/Greek homoglyphs and per-token leetspeak,
  because the raw-ASCII regexes were measured to miss 5 of 9 substitution
  variants. It is used **only** to build a string to match against — never to
  rewrite stored or user-visible text. That boundary is load-bearing: the fold
  mangles Russian prose into word soup, which is fine in a discarded copy and
  would be a serious bug on a persistence path.
  The guardrails we already had, in case they look absent: PII redaction +
  injection detection (`guardrail_service.py`), the evidence gate
  (`evidence_gate.py`), a permission ladder with approval-required tools
  (`tool_gateway.py` + `config/permissions.yaml`), untrusted-data wrapping of all
  RAG/web/tool output (`nodes.py::_wrap_untrusted`), SSRF blocking, and
  role-gated admin routes.
  **`r"jailbreak"` is not a trigger on its own** — it blocked anyone naming the
  topic, including security engineers asking how to defend against it, and a
  guardrail like that gets switched off. It matches the actor position
  ("enable jailbreak", "jailbreak mode", "you are now jailbroken"). No profanity
  filter exists and one is deliberately not wanted; a keyword screen cannot
  catch novel slurs while producing the false positives that get it disabled.
- **`backend/config/` contains exactly one file, `permissions.yaml`, and it is
  the only one with a reader** (`tool_gateway.py:50`). Three dead files were
  deleted 2026-10-01 and must not come back:
  - `model_routing.yaml` — `Router(...)` in `litellm_client.py` is built from
    Python literals with no `config_path`; the yaml had drifted (fast_chat
    llama-3.1-8b-instant vs the code's qwen3.8-27b, an `audio_transcription`
    group that does not exist, cooldown 60 vs 30).
  - `task_contracts.yaml` — describes critic contracts (`constraints` /
    `done_when` / `escalate_when`) and three context-budget tiers (0.60/0.85/
    0.95). `critic.py:14-25` checks a different four-part rubric; `context_
    compiler.py:41` has a single ratio, 0.75. Six numbers, zero live.
  - `eval_criteria.yaml` — G-Eval rubrics (a **deepeval** metric, removed from
    dependencies over CVEs) and a profanity sanitize tier that no code
    implements.
  `tests/test_no_dead_router_config.py` guards the router case only — it
  enumerates **git-tracked** YAML, not `rglob`, because this checkout holds an
  untracked `.kilo/worktrees/debonair-redcurrant/` worktree with a full copy of
  the project and a filesystem scan fails on that copy.

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
- **New cross-cutting modules (added 2026-10, Batch A–D).** All are pure
  functions/classes with injected deps, and all are covered by a
  `tests/test_batch_*.py` whose every fix is verified by reverting it:

  | Module | Role |
  |---|---|
  | `core/spend.py` | Per-run spend meter: token + step ceiling, contextvar-bound, seeded from checkpoint. |
  | `core/credential_check.py` | Boot-time inventory of expected env keys; **never raises at startup** — one broken provider must not stop the process serving everything else. |
  | `services/context_compiler.py` | `ContextCompiler.budget_history()` / `summarize_agent_history()`. New methods only — `compile_context()` consumes ORM `Message` rows and compacts them, destroying history the graph still needs. |
  | `services/memory_lifecycle.py` | B1 write gate + B2 decay. Biases **toward writing**: a missed memory is permanent, a redundant one is deduped downstream. |
  | `services/confidence_action.py` | C1: what to do about a low confidence score. Deterministic and fail-open — no second retry loop, which would double cost on the turns that can least afford it. |
  | `services/tools/content_shape.py` | C2: classify scraped shape; **rejects bad shape and salvages** rather than discarding (discarding would *remove* evidence). Also holds the stdlib `html_to_text()`. |
  | `services/artifact_intent.py` | D3: three-layer "is this turn a document?" decision — explicit request, then answer shape, then one small typed classifier. **The artifact body is never regenerated**; the synthesizer already paid for that text, and a second generation would leave the canvas and the chat permanently disagreeing. |
  | `agents/orchestrator/artifact_node.py` | D3 graph node, `synthesizer → artifact → END`. Opens its own short-lived session (like the memory mirror) and is fail-open: a graph exception there costs the user their whole reply to save them one file. |
  | `services/run_events.py` | The SSE frames a finished run reports (`critique` / `quality` / `artifact`). Extracted from `api/v1/conversations.py` **because it had to be testable, not for tidiness** — as three inline `yield json.dumps(...)` statements in a 300-line async generator, the artifact frame could be deleted outright with no test changing result. |

- **The `html2text` trap.** `services/tools/web_search.py` imported `html2text`,
  which was neither a declared dependency nor installed. The `ModuleNotFoundError`
  was swallowed by the surrounding `except Exception`, so without Firecrawl
  configured *every* scrape returned "Unable to scrape webpage content" and the
  whole direct-HTTP fallback was dead code. Replaced with a stdlib extractor in
  `content_shape.py` rather than adding a dependency. When auditing a fallback
  path, check that its import actually resolves.

- **An SSE event nobody translates looks exactly like one that was never
  emitted.** The backend speaks `data: {"type": …}`; the browser hook reads the
  Vercel AI SDK 3 data-stream dialect (`0:` text, `8:` annotations, `3:` errors),
  and `frontend/src/app/api/chat/route.ts` is the translator between them. A
  new backend event therefore has **two** integration points, and the first one
  is invisible: the row lands in the database, the API works, no test fails, and
  the user is simply never told. `critique` and `quality` are emitted by the
  backend and dropped by the route today — `test_leaves_critique_and_quality_events_untranslated`
  in `src/test/chat-stream-translation.test.ts` exists to make that a decision
  rather than an accident.

- **Artifact identity is the deterministic title, not the message id.** The
  node runs inside the graph, which finishes *before* the assistant message row
  is created (`api/v1/conversations.py`), so `message_id` is unavailable. The
  lookup is `(user_id, conversation_id, title)`
  (`ArtifactRepository.find_by_conversation_title`). That is why
  `artifact_intent.derive_title` must stay deterministic — a title derived from
  the model would give every regeneration a new identity, and
  `artifact_versions` would stay empty forever.

### Frontend layout (`frontend/src/`)

```
frontend/src/
├── app/                     # App Router
│   ├── page.tsx             # landing page
│   ├── signin/page.tsx      # SSO sign-in
│   ├── app/page.tsx         # the main chat shell (sidebar + views)
│   ├── layout.tsx           # root layout: fonts, theme-init script, root metadata
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
- Migrations: Alembic (`backend/migrations/`, head `b1c2d3e4f5a6` —
  **9** revisions: `0001_initial_schema` → `9290fa24428d` → `a1b2c3d4e5f6` →
  `c1d2e3f4a5b6` → `d3e4f5a6b7c8` → `e5f6a7b8c9d0` → `f6a7b8c9d0e1` (closes a
  pre-existing drift) → `a7b8c9d0e1f2` (row-level security) →
  `b1c2d3e4f5a6` (Batch B memory lifecycle columns)).
  `alembic upgrade head` before first boot.
- **The graph is linear and `tests/test_migration_graph.py` enforces it.** It
  was not, and nothing noticed: `a7b8c9d0e1f2` and `b1c2d3e4f5a6` were both
  written against `f6a7b8c9d0e1` and committed independently, giving two heads.
  Alembic refuses to resolve `head` with more than one, so `alembic upgrade
  head` — the documented first-boot command *and* what `make migrate` runs —
  died with "Multiple head revisions are present" before executing a single
  statement. No test touched `migrations/`, and a branched graph is not an
  error at import time, so 940 backend tests stayed green. Both revisions had
  to be applied by naming them explicitly.
- **Alembic must run from `backend/`, and `alembic.ini` depends on it.**
  `script_location = migrations` and `prepend_sys_path` are both resolved
  against the *current working directory*, not against the ini file. So:
  - from the repo root, `script_location` does not resolve ("Path doesn't
    exist: migrations");
  - from `backend/`, `prepend_sys_path` must be `..` (the repo root) so that
    `from backend.app.domain...` — which every revision uses so autogenerate
    compares against the app's own metadata — resolves. With `.` it means
    `backend/`, the path gains `backend.app` instead of `backend`, and **all
    nine revisions fail identically** with `No module named 'backend'` before
    Alembic inspects a statement.
  - **`backend/.env` `DATABASE_URL` points at hosted Supabase, not the local
    container.** Run `docker compose up -d postgres` and export
    `DATABASE_URL=postgresql+asyncpg://nexus:nexus@localhost:5432/nexus_dev`
    before touching alembic, or you will migrate the live database. (This is
    not hypothetical — see §9.16.) **`alembic upgrade`/`downgrade` now refuse a
    non-local host outright** unless `ALLOW_REMOTE_MIGRATIONS=1`, so the
    accidental case is stopped rather than merely documented — see §9.18.
- **The three database DSNs now agree.** `backend/.env.example`,
  `app/core/config.py`'s `DATABASE_URL` default, and the `postgres` service in
  `docker-compose.yml` all resolve to `nexus:nexus@localhost:5432/nexus_dev`.
  They did not: `.env.example` said `postgres:postgres@/nexus_db`, which cannot
  reach the container the same file tells you to start, so copying the example
  to `.env` failed and leaving `.env` alone silently kept the production DSN.
  `tests/test_db_target_guard.py` parses all three and fails if they drift.
- **Row-level security is on.** `a7b8c9d0e1f2` enables RLS on every `public`
  table and revokes the blanket grants Supabase gives `anon`/`authenticated`,
  closing the Shield advisories `rls_disabled_in_public` and
  `sensitive_columns_exposed`. It deliberately does **not** set `FORCE ROW
  LEVEL SECURITY`: the backend connects as the table owner, which bypasses its
  own RLS, and storage calls use `service_role`, which carries `BYPASSRLS`.
  Denying `anon`/`authenticated` removes no capability the app has. A
  `ddl_command_end` trigger covers tables created later, which matters because
  `infrastructure/database/engine.py` calls `create_all` at startup.
- **Index-name convention**: `index=True` on a model column makes SQLModel
  auto-name the index `ix_<table>_<col>`. The migration must use that exact
  name, not merely avoid colliding with it — otherwise autogenerate reports
  permanent drift.
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
- **Researcher (D1)** — `RESEARCH_MAX_SUBQUERIES=3`, `RESEARCH_MAX_SCRAPES=4`,
  `RESEARCH_SCRAPE_CONCURRENCY=3`, `RESEARCH_MAX_REFLECTIONS=1`,
  `RESEARCH_EVIDENCE_FLOOR_CHARS=1200`, `RESEARCH_SYNTHESIS_MAX_TOKENS=1400`,
  `RESEARCH_MAX_MODEL_CALLS=4`. The scrape budget is a **total across all
  sub-queries**, not per sub-query, or breadth multiplies into unbounded spend.
  Decomposition and reflection are each skipped when they cannot pay, so the
  common case costs what it cost before.
- **Run spend ceiling (D2)** — `AGENT_LOOP_MAX_STEPS=8`,
  `AGENT_LOOP_TOKEN_BUDGET=60000`. Every loop in the graph is individually
  bounded and their product is not; this is the only cumulative check.
- **Artifact generation (D3)** — `ARTIFACT_GENERATION_ENABLED=true` (master
  switch; false makes the decision record-only, still logged, nothing written),
  `ARTIFACT_MIN_DOCUMENT_CHARS=1200`, `ARTIFACT_MIN_CODE_CHARS=400`,
  `ARTIFACT_MIN_CODE_SHARE=0.25`, `ARTIFACT_MIN_CLASSIFIER_CHARS=200`,
  `ARTIFACT_ALLOW_CLASSIFIER=true`. The two length floors are **separate on
  purpose**: a prose document is long *because* it is a document, and a code
  file is worth keeping well before it reaches prose length.
  `ARTIFACT_MIN_CODE_SHARE` is the main false-positive guard — without it,
  `"use % to test evenness: ```x % 2 == 0```"` becomes a source file, because
  a closed fence cannot tell a file from an illustration. Fence *count* is the
  wrong discriminator; *proportion of the answer* is the right one. Turning
  `ARTIFACT_ALLOW_CLASSIFIER` off leaves the free signals deciding alone — that
  is how the thresholds were tuned, and it is the setting to reach for if LLM
  spend matters more than recall.
- **HyDE (retrieval)** — `RAG_HYDE_MODE=llm|template|off` (default `llm`),
  `RAG_HYDE_MODEL=fast_chat`, `RAG_HYDE_MAX_TOKENS=220`,
  `RAG_HYDE_TIMEOUT_SECONDS=6.0`. The model call is per *abstract* query only —
  `should_generate_hyde` skips code, path, and keyword lookups before any spend.
  `template`/`off` exist so the cost/benefit is measurable by flipping one
  variable; see §9.19 for why the template is a fallback rather than the
  implementation.
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

## 6. CI / CD (all gates green locally at the D3 commit)

`.github/workflows/`:

- **`ci.yml`** — every push/PR:
  1. `test` job: backend `pytest -m "not e2e"` (unit + behavioral-invariant;
     includes offline arena/conversational/prompt-regression heuristics).
  2. `security` job: bandit over `app/`, pip-audit over the locked venv
     (ignores the one ecdsa advisory), npm audit, **mypy over the 18-file
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
uv run mypy --follow-imports=silent \
  $(grep -v '^\s*#' scripts/mypy_targets.txt | sed 's/\r$//;s|^backend/||')
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
- **`api_router.routes` does not list paths on this FastAPI version.**
  `include_router` leaves `_IncludedRouter` objects that have no `.path`, so
  `{getattr(r, "path", "") for r in api_router.routes}` yields `{""}` and a
  test asserting a route was registered passes for the wrong reason. Use
  `app.openapi()["paths"]`.

## 7. Testing inventory (verified counts)

- Backend: **67 unit test files** + **11 integration** + **3 e2e** under
  `backend/tests/` (pytest). Fakes live in the single module
  `backend/tests/fakes.py` (e.g. `FakeSession`).
- Suite total: **1073 passed, 4 skipped, 9 deselected** for `cd backend &&
  uv run pytest` (which already excludes e2e via `addopts`). The 9 deselected
  are the e2e markers; the `integration` marker is unregistered, so those 11
  files run by default and need Docker up. **If Docker Desktop is not running
  those 11 files produce ~113 `DockerException` setup errors** and the total
  drops to ~946 — that is the environment, not a regression. Verify with
  `docker info` before investigating.
- Frontend: **29 Vitest test files** under `frontend/src/test/` (260 tests) +
  **6 Playwright specs** in `frontend/e2e/`.
- Batch A–D test files: `test_batch_a_wiring.py`, `test_batch_b_memory.py`,
  `test_batch_c_correctness.py`, `test_batch_d_research.py`,
  `test_batch_d_spend.py`, `test_batch_d3_artifact.py`,
  `test_batch_d3_run_events.py`, `test_memory_lifecycle_schema.py`.
  RAG: `test_hyde_generation.py` (31 tests, 15-revert harness — see §9.19).
- `walkthrough.md` once claimed "82 tests" — that is stale. Current counts: see
  above.
- Backend pyproject: `addopts = -v -m 'not e2e'` — plain `pytest` skips e2e.

**The revert check is the part that matters.** Every batch ships with a script
that applies each fix's inverse to the real source, runs the suite, and
requires it to *fail* — then restores the file. A test that passes with and
without the fix is not testing the fix. Nine genuine defects were found this
way that the fixes alone would have hidden, including an `UnboundLocalError`
where a budget stop mid-revision-loop 500'd the request, and two cases where a
wiring test was only a source-grep and passed even with the call's result
discarded.

**A killed revert run leaves a real source file reverted.** The `finally` that
restores it does not run if the process dies, so the next run fails its own
baseline and looks like a genuine test failure. Snapshot the pristine sources
up front and restore them unconditionally when the baseline is red — then say so
rather than debugging a phantom regression. But a snapshot of an *already
reverted* tree restores the damage instead of undoing it, so the harness now
runs a preflight that reports which reverts it finds already applied and
refuses to touch anything. And never run two harnesses against one tree: two
were live at once here, each reverting files the other was restoring, and the
symptoms (a red baseline, then a red suite, then green, then red again with
different tests) looked like three unrelated bugs instead of one.

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
9. **Contextvars must be bound inside the generator and cleared in `finally`.**
   This is already done for A4's request context (bound in the SSE generator,
   cleared at `conversations.py`'s `finally`) and D2's spend meter
   (`bind_spend_meter` / `reset_spend_meter(token)`, cleared in the same
   `finally`). A contextvar outlives the statement that set it, so binding it
   outside the generator — or omitting the reset — leaks one request's state
   into the next request served by the same task. For the spend meter that
   leak is doubly bad: an already-exhausted meter **silently disables the
   ceiling** for the following request. `reset_spend_meter` catches broadly on
   purpose: a token from another context raises `ValueError`, a wrong-typed one
   raises `TypeError`, and dropping the meter is the safe direction.
10. **The spend meter fails open on absence, not on breakage.** No meter bound
    means no ceiling (something that forgot to bind runs unbounded); a meter
    that *raises* propagates (the only realistic cause is a bug in the meter,
    and swallowing it would hide that bug from the test that introduced it).
    Do not re-add a `try/except` around `charge_tokens`/`charge_step`/
    `budget_exhausted` — it also reintroduces a bandit `B110` CI failure.
11. **A budget stop must cost a revision, never the answer.** The synthesizer's
    critic loop tracks `last_draft` separately from `response_text`, because
    `response_text` is only assigned on the paths that *accept* a draft. When
    adding any new early-`break` path to that loop, assign `response_text`.
12. **`nodes.py` contains corrupted box-drawing bytes** on some lines, so `edit`
    anchors containing em-dashes fail there. Use ASCII-only anchors or a
    Python script. In revert scripts use `rindex()` for trailing anchors.
13. **A test can pass for the wrong reason, and a green suite cannot tell you.**
    The most dangerous version is a fixture that never reaches the condition it
    is named for: the single-fence test proved the "code must be dominant" rule
    while the snippet in it was too *short* to be substantial, so deleting the
    dominance check entirely changed nothing. When a test asserts a compound
    condition, build the fixture so each half is independently load-bearing, and
    assert in the fixture that it really is (e.g. `assert len(code) >= 400` and
    `assert share < 0.25`). Three such gaps were found and closed in D3 alone.
14. **Mocking a collaborator hides its logic.** Every node test replaced
    `ArtifactRepository` with a fake, so `find_by_conversation_title`'s actual
    filter never executed — inverting its title predicate passed all of them.
    Fakes are right for the *caller*; when the collaborator's own behaviour
    matters, record the statement it builds. But recording it is not the end of
    it: `str(stmt)` and `stmt.compile().params` together pin the *columns* and
    the *values*, and inverting `==` to `!=` changes neither. The comparison
    operator is the third axis and it has to be asserted on its own — walk the
    `whereclause` for `BinaryExpression`s and check `operator is operators.eq`.
    That gap survived one round of "fix the weak assertion", which is the
    point: the first version of a test for a compound property is rarely the
    one that is actually load-bearing.
15. **A test that never asks the framework whether it accepts your object is
    testing the object, not the integration.** All 18 checkpointer tests
    exercised `ResilientPostgresSaver` directly and passed, while LangGraph was
    throwing it away at `compile()`. When a fix exists to satisfy a *caller's*
    validation, at least one test has to make that call.
16. **`alembic` in this repo talks to hosted Supabase unless you stop it.**
    `backend/.env` carries a real `DATABASE_URL` pointing at
    `aws-0-ap-south-1.pooler.supabase.com`, and `migrations/env.py` reads the
    app settings, so `uv run alembic upgrade head` from `backend/` migrates the
    **live** database. On 2026-10-01 an `upgrade head` intended for the local
    container was pointed at Supabase and applied `a7b8c9d0e1f2 ->
    b1c2d3e4f5a6` there. It was additive and idempotent (`ADD COLUMN IF NOT
    EXISTS`, `CREATE INDEX IF NOT EXISTS`, bounded UPDATEs), no rows were lost,
    and it turned out to be a revision the committed Batch B code already
    required — but it was an unauthorised change to a production database, and
    the right response is to escalate, not to reason about whether it was
    harmless. **This is now enforced, not just documented** — see §9.18.
    - The pooler also rejects asyncpg prepared statements, so a read-only
      inspection script needs
      `create_async_engine(url, connect_args={"statement_cache_size": 0})` or
      it dies with `DuplicatePreparedStatementError`.
17. **A reverted "test" that changes nothing is not a test.** The first attempt
    at the `!res.ok` revert in `listSsoProviders` replaced `return []` with
    `return [] as never[]` and the suite stayed green — correct, because the
    edit was semantically inert. The second attempt (delete the guard) *also*
    stayed green, and that was the real finding: with `Array.isArray` and a
    `try/catch` already in place, the guard was unreachable for every failure
    the app itself produces, so the test was passing for the wrong reason. It
    only became load-bearing once a test served a **503 carrying a valid
    provider list** — the proxy-replays-a-cached-200 case that `Array.isArray`
    cannot filter. Read the revert and ask what it actually changes before
    believing a pass or a miss.
18. **`alembic upgrade`/`downgrade` now refuse a non-local database.**
    `migrations/env.py::assert_migration_target_allowed` parses the DSN via
    `core/db_target.py::describe_database_target` and raises unless the host is
    local (`localhost`, loopback, the compose service names) or
    `ALLOW_REMOTE_MIGRATIONS=1` is set. The incident in §9.16 happened because
    nothing in the toolchain looked; this is the looking.
    - **The gate is on the migration command, not on app boot.** Booting against
      a remote database is how the deployed app runs, so refusing there would
      break production and protect nothing. `main.py` logs the resolved target
      (`database_target_resolved`, plus `database_target_is_not_local`) before
      `init_db()` — before, because `init_db` runs `create_all`, which is itself
      a schema write.
    - **Read-only commands are not gated** (`history`, `current`, `heads`,
      `revision --autogenerate`) — inspecting a remote database is legitimate,
      and gating it would only teach people to set the opt-in and leave it set.
      Offline mode (`--sql`) is exempt because it never opens a connection;
      blocking the preview is how people run the real command unread.
    - **`describe_database_target` treats an unparseable port as remote.**
      `urlsplit("postgresql://u:p@localhost:5432.evil.com/db").hostname` still
      returns `localhost` while `.port` raises, so catching that error and
      keeping the host would report a malformed DSN as confidently local and wave
      it past the gate. `is_local` is true only on a positive match.
    - **Two source-shape assertions were wrong and the revert harness caught
      both.** `assert "database_target_is_not_local" in source` passed after the
      event was renamed to `..._removed`, because the original is a *prefix* of
      it — match the closing quote. And reading a signature with
      `.split("def f", 1)[1].split(":", 1)[0]` yields `"(url"`, so a guard
      against the word `offline` could never fire and reported green against the
      exact edit it was written to catch; use `ast` (§2).
    - The `offline=` keyword argument was removed for the same reason: every test
      passed it explicitly, so `context.is_offline_mode()` ran in production and
      in no test. The tests inject a `context` stub instead — a parameter that
      overrides the value under test is a second source of truth for it.

19. **HyDE is a real model call, and it is fail-open towards the template.**
    `services/rag/query_rewriter.py` used to return a fixed string,
    `"An overview of <query>, including definition, key concepts, workflows, ..."`,
    which is HyDE's *shape* with none of its *mechanism*. A question and its
    answer share little wording ("how do I stop the model looping" versus "add a
    per-run step ceiling and a token budget"), so the entire value of HyDE is
    that the *imagined answer* carries the answer's vocabulary. The template
    carries none of it, embeds close to the original query -- which is already
    variant #1 -- and spent a full hybrid search re-finding chunks that
    `resolve_children_to_parents` dedupes moments later. It survives as
    `RAG_HYDE_MODE="template"` and as the fail-open fallback, because "free,
    instant, always returns text" is the right answer when generation is
    unavailable.
    - `IRewriter.rewrite` is now **`async`**, because producing a hypothesis is a
      model call; it was sync while the hypothesis was a constant. Two
      production call sites: `rag_service.py:79` and `retrieval_guard.py:87` (the
      CRAG corrective pass, already inside an `async def`, so it may spend a call
      too -- bounded by `CRAG_MAX_REVISIONS`).
    - **`asyncio.CancelledError` must not be swallowed by the fail-open handler.**
      It is how a client disconnect and a shutdown reach this code, and it
      inherits `BaseException` precisely so a broad `except Exception` cannot eat
      it. It is caught, documented, and re-raised.
    - **Call `ai_client.completion()`, never `complete()` or the raw router.**
      `charge_tokens` is called in exactly one place, `litellm_client.py:453`,
      chosen so that no call site has to remember to. Reaching past it produces a
      HyDE call that costs money and is invisible to the per-run ceiling in
      `core/spend.py` (see 9.10).
    - **The singleton must be constructed with a real client.**
      `QueryRewriterService()` with no client degrades to the template silently
      and is indistinguishable from working from outside -- which is exactly what
      makes the rest of the module testable without a network, and means an
      unwired `query_rewriter_service` would pass every other test.
      `test_the_production_singleton_is_actually_wired_to_a_client` is the only
      test that can catch it. Same shape as 9.14.
    - `RAG_HYDE_MODE` is `llm` | `template` | `off`; the other two exist so the
      cost/benefit is measurable by flipping one variable rather than by
      reverting code. `RAG_HYDE_MAX_TOKENS` (220) and
      `RAG_HYDE_TIMEOUT_SECONDS` (6.0) are ceilings, not suggestions -- this runs
      in front of every abstract query.

## 9b. Already solved — do not re-propose

Bounded revision loop with force-accept; LLM retry/empty-response handling +
circuit breaker; token limiter (now wired to real usage); SSRF guard;
structured citations; search provider ladder; checkpointer durability. These
were all re-verified against source during the Batch A–D work.

## 10. Where is the source of truth for each doc

| Concern | Source of truth |
|---|---|
| API endpoints | live FastAPI `app.openapi()` (99 paths, 116 ops) — regenerate `docs/api-reference.md` from it |
| Tables | `backend/app/domain/**/models.py` (35) |
| Keyboard shortcuts | `frontend/src/components/{CommandPalette,ArtifactCanvas,ChatInput}.tsx` |
| Frontend design system | `frontend/src/app/globals.css` + `frontend/src/lib/theme.ts` + `docs/frontend-design.md` |
| Alerts/SLOs/runbooks | `docs/alerting/alert-rules.yml` ↔ `docs/slo.md` ↔ `docs/runbooks/` |
| Versions | `backend/pyproject.toml`, `frontend/package.json` |
| CI behavior | `.github/workflows/*.yml` |
| Config surface | `backend/app/core/config.py` + `backend/.env.example` |

---

_Last updated: 2026-10-02 (Batches A–D incl. D3 artifacts; then migration-graph
linearization, `/auth/oauth/providers` + sign-in wiring, model remap, Makefile
gates, admin user-erasure endpoint, all three dead config files removed,
guardrail Unicode-evasion fix, `docs/api-reference.md` resync; then the
database-target guard — `.env.example`/config/compose DSNs reconciled and
`alembic upgrade`/`downgrade` made to refuse a non-local host without
`ALLOW_REMOTE_MIGRATIONS`; then LLM-backed HyDE — `IRewriter.rewrite` made
`async`, the fixed template demoted to a mode and a fail-open fallback).
Regenerate counts (tables/endpoints/tests) from code rather than trusting any
static number here — and verify code-shape claims with `ast`, not regex, since
this repo has CRLF checkouts._