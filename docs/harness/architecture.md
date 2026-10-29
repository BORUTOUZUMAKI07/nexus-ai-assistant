> Part of the repo reference. Moved from `AGENTS.md` §3; the section
> numbering is preserved so existing `§3.N` references still resolve.
> Start at [`AGENTS.md`](../../AGENTS.md) for the session contract.

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
│   │                        #   slices, fairness), evaluation/ (heuristics, ragas,
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
  and a paid-LLM evaluation framework were **removed** in 2026-09 due to
  unfixed CVEs — see comments in `pyproject.toml`).
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
  | `services/run_events.py` | The SSE frames a finished run reports (`critique` / `quality` / `artifact` / `citation`). Extracted from `api/v1/conversations.py` **because it had to be testable, not for tidiness** — as three inline `yield json.dumps(...)` statements in a 300-line async generator, the artifact frame could be deleted outright with no test changing result. `citation_payloads()` is the C3 addition (§9.28) and the single source both the frames and the message row read. |

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
│   ├── signin/page.tsx      # OAuth sign-in
│   ├── app/page.tsx         # the main chat shell (sidebar + views)
│   ├── layout.tsx           # root layout: fonts, theme-init script, root metadata
│   └── api/**/route.ts      # BFF route handlers → proxy to backend
├── components/              # ChatArea, ChatInput, Sidebar, CommandPalette,
│   │                        #   ArtifactCanvas, KnowledgeView, SettingsView,
│   │                        #   UsageView, AdminView, PlanHistory, AuthModal,
│   │                        #   CitationInspector, SavedArtifacts, ui/*
├── hooks/useNexusChat.ts    # chat state machine (SSE consumption), and since
│                            #   C3 the *only* writer to the transcript:
│                            #   hydrateThenRejoin / historyOwnerRef /
│                            #   transcriptEpochRef / isHydrating (§9.28)
├── lib/                     # api.ts, auth.ts, models.ts, proxy.ts, theme.ts,
│                            #   utils.ts, chatStream.ts (one read loop for both
│                            #   entry points), conversationHistory.ts (rows →
│                            #   NexusMessage, plus hasAnswer)
├── proxy.ts                 # edge middleware: server-side session gate for /app
├── lib/proxy.ts             # shared BFF helper (cookie JWT → Authorization: Bearer)
└── test/                    # Vitest: components/, hooks/, lib/, mocks/
                            #   (MSW), route-handlers, api-parity/api-wiring
```

Frontend facts verified:

- **Next.js 16.4.2** with **React 19.2.8** (both pinned — this is not Next 13
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
- **Design system is "Refero" — Paper (light, default) + Void (dark) + ONE
  accent (Electric Acid Lime), never the old black-only look.** Tokens live in
  `frontend/src/app/globals.css` (`:root` Paper, `[data-theme="dark"]` Void),
  driven by `frontend/src/lib/theme.ts` and the pre-paint `THEME_INIT_SCRIPT`;
  persisted in `localStorage` (`nexus-theme`), deliberately **not** from backend
  `UserSettings.theme`. **There are no accent swaps** — the cyan/violet/emerald
  `data-accent` blocks, the Appearance accent picker, the command-palette accent
  entries and the `nexus-accent` key were all removed 2026-10-14 at the product
  owner's request (\"only acid lime is good\"); `theme.ts` writes no `data-accent`
  and the init script strips any stale one. The brand mark is the Nexus
  node-glyph on a lime tile (`frontend/src/components/NexusMark.tsx` for the
  assistant avatar + landing navbar; `public/nexus-logo.svg`, `src/app/icon.svg`
  and `src/app/favicon.ico` for the sidebar/sign-in and the browser tab). The
  old lucide `Bot` avatar and the purple→cyan tile are gone.
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
