# Nexus AI Assistant — API & FastMCP Reference

> Generated from the **live OpenAPI schema** (`app.openapi()` at commit
> `9b6181a`): **100 paths · 117 operations**, all under `/api/v1` except
> where noted. This is a hand-maintained summary; the interactive Swagger UI
> at `/docs` is always authoritative.
>
> Regenerate the counts rather than trusting them: `app.openapi()["paths"]`.
> Note that `api_router.routes` is useless for this — on this FastAPI version
> `include_router` leaves `_IncludedRouter` objects with no `.path`, so
> `{r.path for r in api_router.routes}` yields `{""}` and a check built on it
> passes for the wrong reason.

## Base URLs

- Local backend: `http://localhost:8000`
  - Swagger UI: `http://localhost:8000/docs`
  - OpenAPI JSON: `http://localhost:8000/openapi.json`
  - Health: `GET /health`, `GET /health/ready`
  - Prometheus metrics: `GET /metrics` (not in the schema)
  - FastMCP (auth-gated): `http://localhost:8000/mcp`
- Frontend BFF: `http://localhost:3000` — route handlers under
  `frontend/src/app/api/**` proxy to the backend; the browser only hits :3000.

## Authentication

- `POST /auth/register` — create account (triggers email verification)
- `POST /auth/login` — credentials → JSON `{access_token, refresh_token}`;
  the frontend BFF stores them in httpOnly cookies (`nexus_access_token`,
  refresh) on the client side
- `POST /auth/refresh` — rotate refresh token
- `POST /auth/logout` — invalidate current session
- `GET /auth/me` — current user profile
- `POST /auth/verify-email` · `POST /auth/resend-verification`
- `POST /auth/forgot-password` · `POST /auth/reset-password`
- 2FA (TOTP): `POST /auth/2fa/setup`, `POST /auth/2fa/enable`,
  `POST /auth/2fa/disable`, `POST /auth/2fa/verify`, `GET /auth/2fa/status`
- OAuth (PKCE): `GET /auth/oauth/providers` → **unauthenticated** list of the
  providers this deployment can actually complete a login with, so the sign-in
  page renders exactly those buttons instead of a hardcoded pair. Declared
  *before* `/oauth/{provider}` on purpose: FastAPI matches in registration
  order, and a literal path registered after a catch-all is captured as a
  provider named `providers` and answered with the "unconfigured" 404 —
  indistinguishable from SSO being off, with no test red. Returns names and a
  boolean only.
- `GET /auth/oauth/{provider}` (`google` | `github`) → returns the provider
  authorization URL; `POST /auth/oauth/{provider}/callback` → exchanges the
  provider code for a session (both 404 while that provider is unconfigured)

## Conversations & messages

| Method | Path | Description |
|---|---|---|
| GET | `/conversations` | list user's conversations |
| POST | `/conversations` | create conversation |
| GET | `/conversations/search` | search conversations |
| GET/PATCH/DELETE | `/conversations/{conversation_id}` | get / rename / delete |
| POST | `/conversations/{conversation_id}/fork` | branch from a message |
| POST | `/conversations/{conversation_id}/messages` | send message (sync) |
| POST | `/conversations/{conversation_id}/messages/stream` | **SSE chat stream** |
| POST | `/conversations/{conversation_id}/messages/{message_id}/feedback` | thumbs up/down |
| POST | `/conversations/{conversation_id}/hitl` | resolve HITL approval |
| POST | `/conversations/{conversation_id}/stream` | legacy stream alias |
| GET | `/conversations/{conversation_id}/runs/{run_id}/stream` | **re-attach to a run** (replay + tail; honours `Last-Event-ID`) |
| POST | `/conversations/{conversation_id}/plan` | generate a plan (plan mode) |
| GET | `/conversations/{conversation_id}/plans` | list conversation plans |

## Plans & artifacts

| Method | Path | Description |
|---|---|---|
| POST | `/plans/{plan_id}/approve` | approve a generated plan |
| POST | `/plans/{plan_id}/reject` | reject a plan |
| GET/POST | `/artifacts` | list / create artifacts |
| GET/DELETE | `/artifacts/{artifact_id}` | fetch / delete |
| POST | `/artifacts/{artifact_id}/versions` | add a version |

## Files & RAG

| Method | Path | Description |
|---|---|---|
| GET | `/files` | list user's files |
| POST | `/files/upload` | upload → chunk → embed → index (201 sync, or **202** with `Location` when `ASYNC_INDEXING=true`) |
| POST | `/files/rag/query` | query the RAG index |
| GET | `/files/{file_id}/chunks` | chunk hierarchy |
| GET | `/files/{file_id}/index-status` | poll async indexing job |
| DELETE | `/files/{file_id}` | delete + clean vector embeddings |

## Tools & execution

| Method | Path | Description |
|---|---|---|
| GET | `/tools` | list available tools |
| POST | `/tools/execute` | execute a tool |
| POST | `/tools/approval` | approve/deny a tool execution |
| POST | `/tools/elicitations` | start an elicitation |
| POST | `/tools/elicitations/{elicitation_id}/respond` | respond to an elicitation |

## Settings, usage, account

| Method | Path | Description |
|---|---|---|
| GET/PUT | `/settings` | user settings |
| GET/POST | `/settings/keys` | BYOK provider keys (Groq/OpenRouter/OpenAI, Fernet-encrypted) |
| GET/POST | `/settings/memories` · `DELETE /settings/memories/{memory_id}` | mem0 memory |
| GET | `/usage/summary` | token/cost/quota summary |
| GET | `/usage/evaluations` | evaluation history |
| GET | `/account/export` · `DELETE /account` | data export / account deletion |
| GET | `/prompts/templates` · POST `/prompts/templates` · `GET /prompts/skills` | prompt templates & skills |

## Orgs, shares, webhooks, audio

| Method | Path | Description |
|---|---|---|
| GET/POST | `/orgs` · GET/DELETE `/orgs/{org_id}` · `POST /orgs/{org_id}/leave` | organization CRUD |
| POST | `/orgs/{org_id}/invites` · `POST /orgs/invites/accept` · `GET/DELETE /orgs/{org_id}/members[/{user_id}]` · `GET /orgs/{org_id}/usage-summary` | membership |
| GET/POST/DELETE | `/shares/{conversation_id}` | public conversation sharing (TTL) |
| GET/POST | `/webhooks` · PATCH/DELETE `/webhooks/{endpoint_id}` · `GET /webhooks/{endpoint_id}/deliveries` · `POST /webhooks/{endpoint_id}/redeliver` | webhooks |
| GET | `/audio/speech` | TTS (edge-tts, key-less) |
| POST | `/audio/transcribe` | STT |

## Admin (`get_current_admin` required)

- **Users**: `GET /admin/users`, `POST /admin/users/{user_id}/toggle-status`,
  `DELETE /admin/users/{user_id}` (delegates to the same dependency-ordered
  cascade as `DELETE /account`; refuses self-deletion and refuses to remove the
  last *active* admin; audit is fail-open)
- **Hooks**: `GET/POST /admin/hooks`, `PUT/DELETE /admin/hooks/{policy_id}`,
  `POST /admin/hooks/reload`
- **Monitoring**: `GET /admin/monitoring/{drift,observability,viewer,viewer-data,slices,fairness,bandits}`
- **Evaluation**: `POST /admin/evaluation/{arena,conversational,deepeval,prompt-regression,quality,rag,redteam}`
- **Optimization**: `POST /admin/optimization/run`, `GET /admin/optimization/runs`
- **Audit**: `GET /admin/audit`, `GET /admin/audit-logs`, `GET /admin/audit/eu-act`,
  `GET /admin/audit/redteam`
- **System**: `GET /admin/system-status`

## Health & infra

- `GET /health` — liveness
- `GET /health/ready` — readiness (DB/Redis/Qdrant checks)
- `GET /` — root banner
- `GET /metrics` — Prometheus text format (0.0.4), family names
  `nexus_requests_total`, `nexus_errors_total`, `nexus_latency_seconds`,
  `nexus_latency_avg_seconds`

## FastMCP (`/mcp`)

A FastMCP server exposing agent tool execution (web search, code execution,
file IO, elicitations). Every request is gated — a Bearer JWT, the
`nexus_access_token` cookie, or the `x-nexus-mcp-key` header
(`MCP_API_KEY` setting) must validate (`MCP_AUTH_ENABLED=True`).

- Transport: HTTP + SSE via `mcp.http_app()` (falls back to `sse_app()`).
- Resources: `nexus://conversations/{id}`, `nexus://files/{id}`,
  `nexus://prompts/{name}`
- Capabilities: **Elicitations** supported (structured human input via the
  signed REST bridge), **Roots** declared, **Sampling** not supported
  (client-owned; verified in `backend/app/mcp/server.py:get_advanced_capabilities`).

## Conventions

- Auth: `Authorization: Bearer <jwt>`. The frontend BFF reads the
  `nexus_access_token` cookie and forwards it as the Bearer header; the API
  itself only accepts the header (cookies are honored only by the `/mcp`
  middleware).
- Errors: standard FastAPI `{detail}` shape; 401/403/404/409/422/429.
- Idempotency: send `Idempotency-Key` on mutating requests (guard in
  `deps.py`).
- Rate limits: 429 responses when exceeded (Redis sliding window, fail-open).
- File uploads: multipart; `MAX_UPLOAD_SIZE_MB` enforced before buffering.
- Every documented route above was confirmed present in the live schema; the
  counts at the top were regenerated from `app.openapi()` on 2026-10-04
  (100 paths / 117 operations). Regenerate from `/openapi.json` if it drifts.
- **Stream rejoin**: `POST /conversations/{id}/stream` returns `X-Nexus-Run-Id`.
  `GET /conversations/{id}/runs/{run_id}/stream` re-attaches to that run and
  honours `Last-Event-ID`; it 404s with `{"detail": "run_not_found"}` for a run
  that is not yours, and that 404 is distinguishable on purpose — the frontend
  forgets the stored id and drops the empty bubble rather than showing an error
  about the user's own conversation.