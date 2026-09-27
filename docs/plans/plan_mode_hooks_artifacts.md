# Implementation Plan — Plan Mode + Lifecycle Hooks + Server-Side Artifacts

Goal: implement all three previously-discussed features from the Claude-feature audit. Backend-first,
matching existing SQLModel/domain/service/API patterns; frontend Next.js (App Router, proxy pass-through)
second; fake-based unit tests only (no Docker), full suite + typecheck + lint green at the end.

## 0. Non-negotiable conventions (verified in code)

- Models: `class X(SQLModel, table=True)` + `__tablename__`; UUID pk `default_factory=uuid.uuid4`;
  timestamps naive UTC `datetime.now(UTC).replace(tzinfo=None)`; JSONB via `sa_column=Column(JSONB)`;
  no Python enums (str + description comments); no `relationship()`.
- Repos: `BaseRepository[Model]`, `await self.session.exec(select(Model).where(...))`, `.first()`/`.all()`.
- Services: structlog `logger.<lvl>("event", key=value)`; injectable transport deps for tests.
- Routes: `from backend.app.api.deps import get_current_user/get_db/...`; admin via `get_current_admin`
  (hand-built dict responses, no response_model in admin.py). Register new routers in `api/v1/api.py`.
- Models must be imported in `infrastructure/database/engine.py` (create_all) **and**
  `migrations/env.py` (autogenerate); add a hand-written Alembic revision (down_revision `9290fa24428d`).
- Tests: `from fakes import FakeSession` (bare import), synchronous `def test_*` + `_await(...)`,
  ASGI tests like `tests/test_observability_viewer.py` (dependency_overrides + ASGITransport).
- No new third-party dependencies. Queries must be fake-testable (plain `select(Model)`, no `func.sum`).

---

## FEATURE 1 — Plan Mode (plan-then-approve)

Design: plan generation is a **service-driven** flow (does NOT touch LangGraph). Creating a plan uses a
direct LLM planner call; executing after approval reuses the existing streaming chat in agent mode with the
approved plan embedded as guidance. Per-tool human approval already exists (HITL) and stays the
per-step gate during execution. No `mode` Literal changes needed on the stream endpoint.

### Backend — new `backend/app/domain/plan/`
- `models.py` → `Plan` (table `plans`): `id, conversation_id(FK conversations, idx), user_id(FK users, idx),
  title, summary(str|None, Text), steps(list[str] JSONB), status(str, default "pending",
  desc "pending | approved | rejected"), decision_reason(str|None), created_at, updated_at, decided_at(dt|None)`.
- `schemas.py` (Pydantic v2, `ConfigDict(from_attributes=True)`): `PlanRequest{task: str(min_length=1,max_length=8000)}`,
  `PlanRejectRequest{reason: str|None}`, `PlanResponse`, `PlanDetailResponse(PlanResponse)`.
- `repository.py` → `PlanRepository(BaseRepository[Plan])`:
  - `create(conversation_id, user_id, title, summary, steps) -> Plan`
  - `get_by_id(plan_id, user_id=None) -> Plan|None` (owner-scope when user_id)
  - `list_for_conversation(conversation_id, user_id, limit=20) -> list[Plan]`
  - `update_status(plan, status, reason=None) -> Plan` (sets decided_at on terminal states)
  - `delete_for_conversation(conversation_id)` + `delete_for_user(user_id)` (plain `delete()`).

### Backend — `backend/app/services/plan_service.py`
`PlanService(session)` (repo injected `repo=None` for tests):
- `prepare_plan(conversation_id, user_id, task) -> Plan`:
  - Load recent context via `ConversationRepository(session).get_messages(conversation_id, limit=8)`;
  - `_generate_plan(task, history) -> (title, summary, steps)`: call `ai_client.completion(...)` with a
    planner system prompt requesting strict JSON `{"title","summary","steps":[...]}` (2–6 steps). Robust
    parse (strip enclosing fenced code). **On any LLM/parse failure → fail-open heuristic**: title = first
    60 chars of task, steps = sentence-split of task (≤6 steps), log `plan_heuristic_fallback`. 
    (Verify `ai_client.completion` signature before coding — same call style as `messages.py`.)
  - persist `Plan(status="pending")`.
- `approve(plan_id, user_id) -> Plan`: owner-scoped; if status != "pending" raise
  `ApprovalConsumedError("Plan ... already resolved.")` (409); set approved + decided_at.
- `reject(plan_id, user_id, reason=None)`: same with status "rejected".

### Backend — `backend/app/api/v1/plans.py` (register in api.py)
- `POST /conversations/{conversation_id}/plan` — body `PlanRequest`, `get_current_user`,
  IDOR via `conv_svc.get_conversation(..., user_id=current_user.id)` → 404; returns `PlanDetailResponse`.
- `GET /conversations/{conversation_id}/plans` — list (IDOR same), newest first.
- `POST /plans/{plan_id}/approve` — returns `PlanDetailResponse`.
- `POST /plans/{plan_id}/reject` — body `PlanRejectRequest` optional.
- Add `get_plan_service` dep in `api/deps.py` (lazy import, matches `get_tool_service` shape).
- Exceptions mapped: `ResourceNotFoundError`→404, `ApprovalConsumedError`→409,
  `ToolExecutionError`-style LLM failure → handled by heuristic (no 5xx for plan gen).

### Backend — cleanup + registration
- `ConversationRepository.delete` (conversation/repository.py): delete `Plan` rows (`conversation_id == conv_id`)
  before messages (add to the listed cascade; plans FK → conversations only).
- `AccountService.delete_account`: add step deleting `Plan` rows by `user_id`.
- `engine.py` + `migrations/env.py`: import `backend.app.domain.plan.models`.
- Migration `versions/<rev>_add_plans_hooks_artifacts.py` (new tables for all three features, `op.create_table`
  + `op.create_index`, FK helper style from `9290fa24428d`).

### Frontend
- `lib/api.ts`: `Plan` types + `createPlan(conversationId, task)`, `approvePlan(id)`, `rejectPlan(id, reason?)`.
- New proxy routes (thin, follow existing patterns; webserver `nodejs`):
  `src/app/api/plans/route.ts` (POST create: body `{conversationId, task}` → backend
  `/conversations/{id}/plan`), `src/app/api/plans/[id]/approve/route.ts` (POST),
  `src/app/api/plans/[id]/reject/route.ts` (POST).
- `components/ChatInput.tsx`: `SendMessageOptions` gains `planMode?: boolean`; new toolbar toggle chip
  "Plan" (`ClipboardList` icon) — prop `planMode`/`onTogglePlanMode`.
- `hooks/useNexusChat.ts`: extend `SendMessageOptions` with `planPreamble?: string`; in `sendMessage`
  body build, if `planPreamble` given, prefix user content with it. (mode unions unchanged.)
- `app/app/page.tsx`: plan state `planDraft`; in `handleSendMessage` when `options.planMode`:
  `createPlan(...)` → set `planDraft`; no direct send. Pass `planDraft` + `onResolvePlan` to `ChatArea`.
  `onResolvePlan("approve")`: `approvePlan` → `chat.sendMessage(task, { mode:"agent", planPreamble })`.
  `onResolvePlan("reject")`: `rejectPlan` → `chat.sendMessage(task, { mode:"normal" })`.
- `components/ChatArea.tsx`: `PlanReviewCard` rendered above messages when `planDraft` (modeled on the
  HITL card, lines ~584-662): title, summary, numbered steps, Approve & Execute / Reject / Dismiss.
  New props `planDraft?` + `onResolvePlan?`.
- Tests: backend `tests/test_plan_mode.py` (service: generate/heuristic fallback/approve/reject/IDOR;
  API via ASGI override + monkeypatched planner). Frontend: `chat-input` toggle test,
  `chat-area` plan-card test, `api` helper tests, MSW handlers for plan routes.

---

## FEATURE 2 — Lifecycle Hooks (pre/post tool policy: block / redact / log)

Design: policy hooks enforced at the single choke point for ALL tool execution —
`ToolGateway.execute_tool`. Active hooks are cached in an in-memory registry (DB-free evaluation),
reloaded from the DB on admin CRUD and at app startup (fail-open when DB unavailable).
Global (NULL org) hooks always apply; org-scoped hooks match when the caller supplies org context.

### Backend — new `backend/app/domain/hook/`
- `models.py` → `HookPolicy` (table `hook_policies`): `id, organization_id(FK orgs, idx, NULL=global),
  name(str, unique, idx), description(str|None), hook_event(str, default "tool.pre_execute",
  desc "tool.pre_execute | tool.post_execute"), tool_pattern(str, default "*"), action(str, default "log",
  desc "log | block | redact"), redact_keys(list[str] JSONB default []), enabled(bool default True),
  created_at, updated_at`.
- `schemas.py`: `HookCreate{name, description?, hook_event="tool.pre_execute", tool_pattern="*",
  action="log", redact_keys=[], organization_id?}`, `HookUpdate{nullable fields}` (both validate
  action/hook_event against allow-lists with `Field(pattern=...)`).

### Backend — `backend/app/services/tools/hook_registry.py` (no DB at eval time)
- Module-level `_snapshot: list[dict]` (plain dicts: org_id, name, event, pattern, action, redact_keys).
- `set_snapshot(rows)` (tests/startup), `clear_snapshot()`, `async reload(session)` → plain
  `select(HookPolicy).where(HookPolicy.enabled == True)` → snapshot of dicts.
- `evaluate_pre(tool_name, arguments, user_id, org_ids=None) -> dict`:
  - match = event=="tool.pre_execute" AND (pattern=="*" or pattern==tool_name) AND
    (org None or org in org_ids). No matches → pass-through `{"blocked":False,"redacted_args":None,...}`.
  - if any `action=="block"` → `{"blocked":True,"reason":"Blocked by hook '<name>': ...","blocked_by":name,
    "redacted_args":None,"matched":[...]}`.
  - if any `action=="redact"` → redact `arguments` for listed `redact_keys` using
    `backend.app.core.redaction.redact_value` (per-key str replacement), return `redacted_args`.
  - always structlog `hook_policy_applied` when matched. Fail-open: empty snapshot → pass-through.
- `evaluate_post(tool_name, user_id, result)` → same matching on "tool.post_execute"; structlog only.

### Backend — hook into `backend/app/services/tools/tool_gateway.py`
Inside `execute_tool`, after permission check (line ~113) and before rate-limit/dispatch:
```
decision = hook_registry.evaluate_pre(tool_name, arguments, user_id)
if decision["blocked"]:  logger.info("tool_blocked_by_hook", ...)
    return {"status":"blocked","tool_name":tool_name,"error":decision["reason"],
            "blocked_by":decision["blocked_by"],"duration_ms":0.0}
arguments = decision.get("redacted_args") or arguments
```
After dispatch success/failure (in `execute_tool`, before returning) call `hook_registry.evaluate_post(...)`.
(Approval-required tools: the "requires_approval" early-return happens BEFORE hooks — that's fine, no
execution is happening yet; hooks re-run at execution time in the approve path, which also goes through
`execute_tool`.)

### Backend — `backend/app/services/hook_service.py`
`HookService(session)`:
- `create(payload) -> HookPolicy` (name validate non-empty/unique → 409 `ValueError` style),
  `list_policies() -> list[HookPolicy]`, `update(hook_id, payload) -> HookPolicy` (partial),
  `set_enabled(hook_id, enabled)`, `delete(hook_id) -> bool`.
- Every mutator calls `await hook_registry.reload(session)` afterward.
- Orgs for eval: `org_ids_for_user(user_id) -> list[UUID]` via plain
  `select(OrganizationMember.organization_id).where(OrganizationMember.user_id == user_id)`.

### Backend — API in `backend/app/api/v1/admin.py` (admin-guarded, hand-built dicts)
- `POST /admin/hooks` (HookCreate) → 201 dict; `GET /admin/hooks` → list dicts;
  `PATCH /admin/hooks/{hook_id}` (HookUpdate) → dict; `DELETE /admin/hooks/{hook_id}` → 204.
- `ToolService.execute_tool` + `approve_tool_call`: after `tool_gateway.execute_tool`, if
  `result["status"] == "blocked"` → raise `ToolPermissionError(f"Tool '...' blocked by hook policy: ...")`
  (403) instead of returning the blocked envelope to the client (message surfaced, `tool_call_id` absent).
- `main.py` lifespan: after `init_db()`, best-effort `await hook_registry.reload(session)` in try/except
  (fail-open).

### Frontend — admin hooks tab
- `lib/api.ts`: `HookPolicy` types + `listHooks/createHook/updateHook/deleteHook`.
- Proxy: `src/app/api/admin/hooks/route.ts` (GET/POST), `src/app/api/admin/hooks/[id]/route.ts`
  (PATCH/DELETE with `type Ctx = { params: Promise<{ id: string }> }`).
- `components/AdminView.tsx`: add `"hooks"` tab (`Tab` union + `tabButton` + `fetchAdminData` branch),
  table (name, event, tool pattern, action, enabled) + toggles, delete, and a create form
  (name / event select / tool pattern / action select / redact keys CSV) modeled on SettingsView inputs.
- Tests: backend `tests/test_hooks.py` (registry block/redact/log matching incl. global vs org;
  HookService CRUD + reload; gateway block envelope; admin API ASGI 403 non-admin + CRUD via overrides).
  Frontend: `admin-view` hooks tab test, `api` helpers, MSW handlers.

---

## FEATURE 3 — Artifacts Backend Builder (persisted + versioned)

### Backend — new `backend/app/domain/artifact/`
- `models.py`:
  - `Artifact` (table `artifacts`): `id, user_id(FK users, idx), conversation_id(FK conversations, idx, nullable),
    message_id(UUID|None, nullable), title(str), language(str default "markdown"), mime_type(str default
    "text/plain"), content(str, Text), version(int default 1), created_at, updated_at`.
  - `ArtifactVersion` (table `artifact_versions`): `id, artifact_id(FK artifacts, idx, not null),
    version(int), title(str), language(str), mime_type(str), content(str, Text), created_at`.
- `schemas.py`: `ArtifactCreate{title(min1), language, mime_type, content(min1), conversation_id?, message_id?}`,
  `ArtifactVersionCreate{content(min1), title?, language?, mime_type?}`,
  `ArtifactResponse`, `ArtifactDetailResponse(ArtifactResponse)` with `versions: list[...]`.
- `repository.py` → `ArtifactRepository(BaseRepository[Artifact])`:
  `create(...)`, `get_by_id(artifact_id, user_id=None)`, `list_for_user(user_id, conversation_id=None, limit=50)`,
  `add_version(...)`, `list_versions(artifact_id)`, `delete_artifact(artifact)` (versions first),
  `delete_for_conversation(conversation_id)`, `delete_for_user(user_id)` (versions via `in_` subquery).

### Backend — `backend/app/services/artifact_service.py`
`ArtifactService(session)`:
- `create(user_id, payload) -> Artifact` (store content in DB — no blob storage; max content 200_000 chars → `ValueError` 422).
- `list(user_id, conversation_id=None)`, `get(artifact_id, user_id) -> (artifact, versions)`,
- `add_version(artifact_id, user_id, payload) -> Artifact` (owner-scoped; keep previous content in an
  `ArtifactVersion` row with version n, bump `Artifact.version += 1`, update content/title/language/mime).
- `delete(artifact_id, user_id) -> bool`.

### Backend — `backend/app/api/v1/artifacts.py` (register in api.py; files.py conventions)
- `POST /artifacts` (201, `response_model=ArtifactResponse`), `GET /artifacts?conversation_id=&limit=` (list,
  `response_model=list[ArtifactResponse]`), `GET /artifacts/{artifact_id}` (`ArtifactDetailResponse`),
  `POST /artifacts/{artifact_id}/versions` (`ArtifactDetailResponse`), `DELETE /artifacts/{artifact_id}` (204).
- All `get_current_user`; owner-scoped; 404 via `ResourceNotFoundError`.
- MCP `create_artifact` tool in `backend/app/mcp/server.py` (own session via `async_session_factory()`,
  mirrors `request_user_input` at lines 243-274) so an agent can build artifacts.

### Backend — cleanup + registration
- `ConversationRepository.delete`: delete artifact versions → artifacts for `conversation_id` (before messages).
- `AccountService.delete_account`: delete artifact versions → artifacts by `user_id`.
- `engine.py` + `migrations/env.py`: imports for `artifact` (and `hook`, `plan`).
- Migration revision covers artifacts tables.

### Frontend
- `lib/api.ts`: `Artifact {id, title, language, mime_type, content, version, created_at, conversation_id?}` +
  `createArtifact/createArtifactVersion/listArtifacts/fetchArtifact/deleteArtifact`.
- Proxy: `src/app/api/artifacts/route.ts` (GET list w/ query, POST), `src/app/api/artifacts/[id]/route.ts`
  (GET/DELETE), `src/app/api/artifacts/[id]/versions/route.ts` (POST).
- `components/ArtifactCanvas.tsx`: extend `ArtifactItem` (optional `version?`, `persisted?`, `created_at?`);
  show `v{n}` badge + Delete button when `persisted` + `onDeleteArtifact`. (Consumers to update:
  ChatArea.tsx, app/app/page.tsx.)
- `components/ChatArea.tsx`: artifact card gains a "Save" action when artifact is not persisted →
  new prop `onSaveArtifact?(artifact, conversationId)`.
- `app/app/page.tsx`: `handleSaveArtifact` calls `createArtifact({title,language,mime_type,content,
  conversation_id})`, merges echoed (id/version/persisted) artifact into `allArtifacts` and replaces the
  local entry; `handleDeleteArtifact` calls `deleteArtifact` + removes from lists.
- Tests: backend `tests/test_artifacts.py` (service version bump + IDOR + char limit; API ASGI CRUD via
  FakeSession override). Frontend: new `artifact-canvas.test.tsx`, api helpers, MSW handlers,
  chat-area save-button test.

---

## Cross-cutting — tests to keep green
- New backend test files runnable with only `tests/fakes.py` (no Docker):
  `tests/test_plan_mode.py`, `tests/test_hooks.py`, `tests/test_artifacts.py`.
- Verified suite after implementation:
  1. `cd backend && .venv\Scripts\python -m ruff check app tests` on changed files.
  2. `pytest tests -m "not e2e" --ignore=tests/integration` → expect prior 193/4 plus new tests all passing.
  3. `cd frontend && npm test` → all vitest suites (previous 89 + new).
  4. `npx tsc --noEmit` clean; `npx eslint src` — only the 2 pre-existing ChatArea/tsx errors (already present; do not introduce new ones; do NOT "fix" the pre-existing ones unless trivial).

## Live verification (endpoints, real DB)
- Restart dev server (uvicorn reload=False; ~2–4 min to bind :8000).
- With real non-admin creds:
  - `POST /api/v1/conversations/{id}/plan {"task":"..."}` → 200 with plan (+ heuristic fallback since
    free-tier LLM quota is exhausted); `GET /conversations/{id}/plans` lists it (403 for a plan not owned).
  - `POST /api/v1/artifacts` create → list → add version → fetch → delete.
  - `GET /api/v1/admin/hooks` → 403 non-admin; 200 with `[]` as admin? (no admin account in dev DB —
    verify 403 non-admin + run admin CRUD through unit/ASGI tests only, as done for the viewer).
- Frontend plan/artifact/hooks flows are unit-tested; page preview optional via `browser.preview` if a
  session is available.

## Docs to update (final step)
- `docs/claude-features-audit.txt`: flip rows 7 (hooks), 8 (plan mode), 13 (artifacts) to implemented.
- `docs/feature-status-report.txt`: add these three shipped features + new test counts.
- Commit (no push unless asked).

## Risk notes
- Plan generation hits a live LLM → heuristic fallback keeps it functional offline (and unit tests
  monkeypatch the planner).
- Hooks registry is in-memory: single-process apps only (matches current uvicorn deployment); loaded at
  startup + on every admin CRUD.
- Artifact content is stored in Postgres (no blob storage) — fine for docs/code; 200k char cap.
- Frontend lint: do not introduce new errors beyond the 2 known pre-existing.