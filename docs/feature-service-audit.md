# Feature & Service Audit

Read-only audit of all user-facing features and service integrations. Memory-design gaps are tracked separately in `tasks/plan.md`. No files were changed by the audit itself.

Evidence legend:

- **[LIVE]** — browser-verified per `docs/live-verification.md`
- **[UNIT]** — backed by automated tests only
- **[CONFIG]** — wired/configured but no proof of real external ingestion or delivery
- **[STUB]** — present in code but non-functional
- **[DOC]** — documented claim not independently verified

## Severity-ranked findings

### HIGH

**H1 — Two admin trace endpoints are unauthenticated** — `backend/app/api/v1/evaluation.py`, `backend/app/api/v1/api.py`

`router = APIRouter(prefix="/admin/evaluation", ...)` at `evaluation.py:27`. Most endpoints take `get_current_admin`, but `get_trace` (`:234–247`) and `compare_traces` (`:250–284`) declare no auth dependency. `api_router.include_router(evaluation.router)` at `api.py:47` adds no router-level guard. Impact is currently limited because H2 makes the endpoints always 404, but the authorization hole is real. Fix: add `Depends(get_current_admin)` to both handlers or the router.

**H2 — Trace comparison is a permanent stub** — `backend/app/services/observability/trace_store.py`

`get_trace_span()` at `:15–26` unconditionally returns `None`; comment at `:24–25` admits it is a placeholder. `GET /admin/evaluation/traces/{trace_id}` and `GET /admin/evaluation/traces/compare` (`evaluation.py:234–284`) therefore always 404. [STUB]

**H3 — Backend TOTP 2FA has no frontend path (login dead-end)** — `backend/app/api/v1/auth.py`, `frontend/src/app/api/auth/login/route.ts`, `frontend/src/app/signin/page.tsx`

Backend 2FA is fully implemented (`auth.py:324–399`, throttle at `:380–386`). BFF login forwards `{"status":"2fa_required","preauth_token":...}` (`login/route.ts:62–68`), but there are no `/api/auth/2fa/*` BFF routes and no setup/enable/disable/verify UI. `signin/page.tsx:92–94` only shows "Two-factor authentication is required. Sign in with your password to enter your code." — no TOTP input, so a 2FA-enabled account cannot sign in.

### MEDIUM

**M1 — CI secret scan skips whole files containing a common "fake" word** — `.github/scripts/check_secrets.py`

`FAKE_PATTERNS = ["test-secret-key","test_key","example","dummy","fake","xxx","changeme"]` at `:17`; `:34–35` skips the entire file if any of those substrings appears anywhere, before real patterns (`PATTERNS` at `:9–15`) run. Since `example`/`test`/`dummy` are common, a genuine key in such a file passes the scan. Fix: only suppress a match when the fake marker is in the same matched line/value.

**M2 — Outbox "exponential backoff" is a constant 1 second** — `backend/app/services/outbox/service.py`

`RETRY_BASE_SECONDS = 1.0` (`:34`), `RETRY_CAP_SECONDS = 300.0` (`:35`). `retry_delay_seconds()` at `:48–55` returns `min(cap, base ** max(0, attempts-1))`; `1.0 ** n` is always `1.0`. Docstring at `:54` claims exponential backoff. Failed jobs retry every second up to `MAX_ATTEMPTS = 5` (`:33`). Fix: `base * 2 ** max(0, attempts-1)`.

**M3 — Observability stack status reports "live" from configuration alone** — `backend/app/services/observability/viewer.py`

`:38–59` sets `"live"` when `NEW_RELIC_ENABLED && opentelemetry package present && NEW_RELIC_OTLP_ENDPOINT set`; no collector or ingest probe. Admin viewer renders a green "live" badge (`:389–390`, `:443–454`). `docs/observability-deployment.md:161–188` caveats that remote ingestion was never live-verified. [CONFIG/DOC]

**M4 — Dead image-generation config and docs drift** — `backend/app/core/config.py`, `backend/.env.example`, `backend/app/services/tools/image_generation.py`

`IMAGE_DEFAULT_WIDTH`, `IMAGE_DEFAULT_HEIGHT`, `IMAGE_MAX_PROMPT_CHARS`, `IMAGE_REQUEST_TIMEOUT_SECONDS` (`config.py:959–962`, `.env.example:358–363`) are never read. Service hardcodes `MAX_PROMPT_CHARS = 1000` (`image_generation.py:50`), `width=1024, height=1024` (`:85–86`), `timeout=60.0` (`:74`). Env tuning silently does nothing.

**M5 — Sync vs stream chat parity gap (resolved as a documented, deliberate asymmetry)** — `backend/app/api/v1/messages.py`, `backend/app/api/v1/conversations.py`, `backend/app/services/runs/executor.py`

The original finding was that the streaming path — the one the product actually chats through — had no response-cache lookup, no experiment/bandit selection, and no `message.completed` webhook dispatch. The first and third of those have since been fixed in code, so the finding is no longer open:

- **Webhooks — fixed on the primary path.** `execute_run` enqueues `message.completed` as a durable outbox row *after* the assistant row commits, with a fail-open inline fallback for deployment before the outbox migration. Live-verified in `docs/live-verification.md` D7 (a real delivered row, HTTP 200). Guarded by `backend/tests/agents/test_post_answer_outbox_executor.py` and `backend/tests/services/test_post_answer_outbox.py`.
- **Cache invalidation — fixed on the primary path.** The same outbox enqueue drops the exact `(user_id, model, prompt_variant, conversation_id)` semantic-cache bucket, so a stream turn cannot leave a stale answer behind. Guarded by the same two tests.
- **Response-cache lookup/write — sync only, by design.** A streamed turn is an agent run with tool calls and checkpoints; serving a cached blob in place of it would change the SSE contract and skip the work the user is watching. Stream turns still invalidate the cache, so cache *correctness* is shared across both paths.
- **Experiment/bandit variant selection — not deployed.** There is no canary/A/B rollout active, so the run path deliberately uses the `default` prompt variant. Adding variant selection to streaming would introduce a new rollout, not close a parity gap.

Provider prompt-cache accounting (`cached_tokens`, `cache_hit_rate`) is written from `_persist_reply` on the run path (live-verified in D3/G4/G6), so the original claim that cache accounting never fires for normal chat was already stale. The remaining asymmetry is intentional and pinned by `backend/tests/ci/test_docs_consistency.py`; it should not be "fixed" by adding experiments to the stream path.

**M6 — Next.js version drift** — `frontend/package.json`, `docs/deployment-guide.md`

Docs state Next.js 16.4.2 (`:3–12,165`); `frontend/package.json:32` declares `next: ^16.4.6`.

**M7 — Frontend proxy is an optimistic cookie gate** — `frontend/src/proxy.ts`

`:15–30` checks cookie presence only (matcher `:136–141`); it does not validate tokens. Real authorization is server-side in `backend/app/api/deps.py:41–86`, including a token blacklist that fails open on error.

### LOW / INFORMATIONAL

**L1 — Redundant owner-agnostic pre-check in webhook redeliver** — `backend/app/api/v1/webhooks.py:103`, `backend/app/services/webhooks/service.py:183–190,208–227`. `deliveries(endpoint_id, limit=100)` has no owner filter, but actual redelivery (`webhooks.py:106`) calls `redeliver(current_user.id, delivery_id)` which JOINs `WebhookEndpoint.user_id == user_id`; no cross-user redelivery. Harmless extra query / potential timing oracle.

**L2 — OTel collector basicauth format unverified** — `docker/otel-collector-config.yaml:156`, `backend/tests/observability/test_otel_collector_config.py:186–214`. `basicauth/server.htpasswd.inline: "${env:OTLP_AUTH_USER}:${env:OTLP_AUTH_PASSWORD}"` looks like plaintext `user:pass` in an htpasswd field; structural tests cannot catch component startup failure. External verification was unavailable.

**L3 — Evaluation endpoints have no frontend surface** — `/admin/evaluation` suite is API-only and partially stubbed.

**L4 — BFF parity test is narrow** — `frontend/src/test/api-parity.test.ts:1–15` checks BFF route ↔ MSW mock consistency only, not backend route ↔ BFF coverage; this is why H3 went uncaught.

**L5 — Deployment target drift** — `render.yaml:7` says frontend deploys to Vercel; `docs/deployment-guide.md:168–172` recommends self-hosting frontend via Docker.

## Feature & service inventory

### Auth / account
| Feature | Purpose | Key paths | Verification |
|---|---|---|---|
| Email/password sign-in | Session cookies, httpOnly | `backend/app/api/v1/auth.py:101–221`, `frontend/src/app/api/auth/login/route.ts` | [LIVE] |
| OAuth | Provider sign-in + callback | `auth.py`, `frontend/src/app/signin/page.tsx:83–101` | [LIVE] (error paths) |
| TOTP 2FA | Enroll/verify/disable | `auth.py:324–399` | Backend only; frontend missing (H3) |
| Rate limiting | Trusted-proxy IP + per-account buckets | `auth.py:101–151`, `backend/app/infrastructure/resilience/rate_limit.py` | [UNIT] |
| Token blacklist / deps | Access-token validation | `backend/app/api/deps.py:41–86` | [UNIT]; fails open on error |
| Account, orgs, invites | Profile & organization mgmt | `backend/app/api/v1/account.py`, `orgs.py` | [LIVE] (invite) |

### Chat / agents / orchestration
| Feature | Purpose | Key paths | Verification |
|---|---|---|---|
| Streaming chat (primary) | SSE answer stream | `backend/app/api/v1/conversations.py:261–269,405–577`; `frontend/src/app/api/chat/route.ts` | [LIVE] |
| Message actions | Regenerate/edit/etc. | `backend/app/api/v1/messages.py` | [LIVE] |
| Orchestrator graph | Plan → route → subagents → critic → synthesize | `backend/app/agents/orchestrator/{graph,nodes}.py` | [LIVE + UNIT] |
| Modes (Deep/Web/Code/Plan/Fast) | Routing + fast-path | `nodes.py:689–906`, `model_router.py` | [LIVE] |
| ARQ constraint check | Fail-closed safety/tool gating | `nodes.py:730–899` | [UNIT] |
| HITL approval | Tool-call human approval | `nodes.py:947–1031`, `hitl.py` | [UNIT] |
| RAG / CRAG | Local retrieval, critique, web supplement | `nodes.py:1034–1176`, `services/rag/*` | [LIVE + UNIT] |
| Prompt snapshot | Model-visible prompt logging | `nodes.py:1185–1228,1522–1530` | [UNIT] |
| Spend meter / budget | Token + step ceiling | `nodes.py:351–435`, `core/spend.py` | [UNIT] |
| Refusal fallbacks | Relay evidence on model refusal | `nodes.py:1713–1848` | [UNIT] |

### Files / RAG / media
| Feature | Key paths | Verification |
|---|---|---|
| File upload + KB indexing | `backend/app/api/v1/files.py`, `services/rag/*` | [LIVE] |
| Image upload & generate | `backend/app/api/v1/images.py`, `services/tools/image_generation.py` | [LIVE]; config dead (M4) |
| Audio / TTS / voice | `backend/app/api/v1/audio.py`, `frontend/.../chat-input-voice.test.ts` | [LIVE] TTS; short-clip guard [UNIT] only |

### Platform services
| Service | Purpose | Key paths | Verification |
|---|---|---|---|
| Billing / plans / usage | Plans, quotas, cost logs | `backend/app/api/v1/{plans,usage}.py` | [LIVE] panels; free-tier quota [CONFIG] |
| Email / verification | Outbound email | `services/notifications/email.py`; health `_email_status` (`health/dependencies.py:176–181`) | [LIVE] login; `EMAIL_VERIFICATION_REQUIRED=false` in `render.yaml:191` |
| Notifications / outbox | Post-answer jobs | `services/outbox/{service,worker}.py` | backoff bug (M2) |
| Webhooks | Signed HMAC delivery, SSRF guard | `backend/app/api/v1/webhooks.py`, `services/webhooks/service.py:35–283` | [LIVE] after fix; redeliver caveat (L1) |
| Response cache | Semantic cache | `infrastructure/cache/response_cache.py` | [LIVE] via sync path only (M5) |
| Shares / public links | Conversation sharing | `backend/app/api/v1/shares.py` (+`public_router`) | [LIVE] |
| Artifacts / prompts / tools / evaluation / metrics | Various admin APIs | `backend/app/api/v1/{artifacts,prompts,tools,evaluation,metrics}.py` | [UNIT]/API-only; evaluation stubbed (H2/L3) |
| Observability (OTel/New Relic) | Traces, metrics | `services/observability/{otel,trace_store,viewer}.py`, `docker/otel-collector-config.yaml` | [CONFIG] not ingested; status misleading (M3); trace store stub (H2) |
| Health / readiness / dependencies | Probes | `backend/app/main.py`, `services/health/dependencies.py` | DB/Redis/Qdrant/Storage/Mem0 real probes; E2B/Firecrawl/Tavily/NR/LangSmith/email are credential/state-only (`:110–111,151–181,205–206`) |
| MCP | Tool protocol mount | `backend/app/main.py` | [UNIT] |

### Frontend
| Surface | Key paths | Verification |
|---|---|---|
| Pages (chat, knowledge, projects, usage, settings, admin) | `frontend/src/app/**` | [LIVE] |
| BFF routes | `frontend/src/app/api/**/route.ts` | parity test (L4) |
| Proxy gate | `frontend/src/proxy.ts` | optimistic (M7) |

### CI / deployment / docs
- `.github/workflows/ci.yml`: secret scan, ruff, mypy allowlist, backend unit + e2e, frontend typecheck/lint/vitest/build, bandit/pip-audit/npm audit, live LLM-judge gates (`RUN_LLM_EVAL_GATES=1`, `OPENROUTER_API_KEY` at `:271–275`) — every push/PR.
- `.github/workflows/nightly.yml`: smoke checks `/health`, `/health/ready`, `/health/dependencies`, fails on degraded.
- `.github/workflows/release.yml`: builds/pushes backend + frontend images to GHCR with SBOM + SLSA provenance.
- `render.yaml`: production flags `NEW_RELIC_ENABLED=true`, public `NEW_RELIC_OTLP_ENDPOINT`, LangSmith, `POST_ANSWER_OUTBOX_ENABLED=true`, `EMAIL_VERIFICATION_REQUIRED=false`, `RESPONSE_CACHE_ENABLED=true` (`:163–191`); no collector health check, accepted cold-start telemetry loss (`:20–21,150–157`).
- `docker-compose.yml`, `Dockerfile`s, `docker/otel-collector.Dockerfile` reviewed.

## Live-verified vs config-only summary

- **[LIVE]** email/password + OAuth sign-in, streamed chat, message actions, file/image upload, image generation, TTS/voice, Deep/Web/Code/Plan modes, Knowledge/Projects/Usage/Settings/Admin panels, share links, org invite, public share page, webhooks on the stream path (post-fix, D7), provider cache accounting on the stream path (G4/G6), exact/semantic response-cache lookup (sync path only, M5 — deliberate), conversation model pinning, mobile widths.
- **[UNIT / not truly live]** free-tier quota & sampling, microphone accuracy, short-clip voice guard, admin/system paths before role grant, all HITL/ARQ/budget/refusal-fallback logic.
- **[CONFIG / not live]** remote New Relic ingestion, LangSmith, E2B/Firecrawl/Tavily (credential-only health), OTel collector auth (L2), billing/Stripe state where `EMAIL_VERIFICATION_REQUIRED=false`.
- **[DOC only]** Next.js version claims (M6), Vercel frontend deployment (L5).
- **[STUB]** trace comparison endpoints (H2).

## Recommended remediation order

1. **H3** — build the 2FA BFF routes + TOTP UI, or disable 2FA enrollment until the UI exists (currently a login dead-end).
2. **H1 + H2** — add admin auth to trace endpoints; implement or remove `get_trace_span` and the compare feature.
3. **M1** — fix the secret-scan whole-file skip.
4. **M2** — fix the constant 1-second retry delay.
5. **M5** — resolved as a documented asymmetry: webhooks and cache invalidation now run on the stream path via the outbox; response-cache lookup stays sync-only by design and no canary/A/B rollout is deployed. Do not "unify" by adding experiments to streaming.
6. **M3** — make stack status reflect a real probe; correct docs.
7. **M4/M6/M7/L1–L5** — dead config, version drift, proxy semantics, collector auth validation, parity test scope.

Audit performed read-only; no production data or accounts were touched.
