> Part of the repo reference. Moved from `AGENTS.md` §10; the section
> numbering is preserved so existing `§10.N` references still resolve.
> Start at [`AGENTS.md`](../../AGENTS.md) for the session contract.

## 10. Where is the source of truth for each doc

| Concern | Source of truth |
|---|---|
| API endpoints | live FastAPI `app.openapi()` (103 paths, 123 ops) — regenerate `docs/api-reference.md` from it |
| Tables | `backend/app/domain/**/models.py` (38) |
| Keyboard shortcuts | `frontend/src/components/{CommandPalette,ArtifactCanvas,ChatInput}.tsx` |
| Frontend design system | `frontend/src/app/globals.css` + `frontend/src/lib/theme.ts` + `docs/frontend-design.md` |
| Alerts/SLOs/runbooks | `docs/alerting/alert-rules.yml` ↔ `docs/slo.md` ↔ `docs/runbooks/` |
| Versions | `backend/pyproject.toml`, `frontend/package.json` |
| CI behavior | `.github/workflows/*.yml` |
| Config surface | `backend/app/core/config.py` + `backend/.env.example` |
| Upstream capability decisions | `docs/upstream-adoption.md` |

---

_Last updated: 2026-10-21 (the Strix batch was completed **live** and the
cross-project claims were re-read against real source -- §9.51. #14 (FastMCP
lifespan chaining) was proven end to end against the **local** containers:
`StreamableHTTP session manager started` at boot, `GET /health/ready` →
`{"status":"ready","database":"available","mcp_transport":"started"}`, and
`POST /mcp/mcp` `initialize` with the shared key → **200** (`serverInfo:
nexus-mcp`) while no key / a wrong key → **401** — pre-fix the valid-key call
returned **500**. The cross-project re-read found url-shortner carries the same
refresh-revocation-not-cleared-on-sign-in defect that item 15 fixed here (its
`session_revocation.py` has no clear function and `login()` never removes the
marker), so that is the only unhandled P0 across the three trees; and it
**retracted two earlier claims** that are no longer true — civic-issue's OAuth
now reads `email_verified` and implements PKCE + nonce + cookie session
binding, and url-shortner's `is_configured()` does check the client secret. The
consolidated P0–P3 list is in `%TEMP%\opencode\cross-project-improvements.md`.
`revert_strix.py` **27/27** load-bearing; full backend suite **1609 passed, 3
skipped, 9 deselected**; `ruff` 0, `bandit` 0, `pip-audit` exit 0, mypy clean
over the 33-file allowlist. All **uncommitted**.)

_Previously: 2026-10-20 (a single Strix scan against the local app produced
**14 findings, all fixed, each with a revert-verified regression test** -- §9.50.
Items 1–14 were the scan's own findings: python-jose → PyJWT (terminal-release
advisories; the import line is deliberately not a revert surface because
`conftest.py` imports `core.security` at module scope), logout leaving the
refresh token alive, settings mass-assignment, an `X-Forwarded-For` rate-limit
bypass (now trusted-proxy-aware + a hashed per-account bucket), a
forgot-password enumeration oracle, mako ≥1.4.2, a public-share citation leak,
the `dev_link` handoff, TOTP replay (single-use counter), the HITL AST effect
scan, the MCP shared-key path skipping revocation/user checks, MCP identity
tools trusting a caller-supplied identity, username HTML injection, and the
unchained FastMCP sub-app lifespan. **Item 15 is the one this session found by
verifying the sibling project's report against our own source: a successful
sign-in never cleared the `refresh:revoked:` marker**, so after a password reset
every refresh 401'd for `REFRESH_TOKEN_EXPIRE_DAYS` even following a fresh login
-- the url-shortner bug, with a worse trigger. Fixed at the one choke point
(`_issue_token_pair`) via `_clear_refresh_family_revocation`, which lifts the
user-wide marker and its own family's but deliberately not another family's.
Also upgraded `aiohttp`→3.14.5 / `yarl`→1.25.1 (two advisories published since
§9.46) and removed the last two `--ignore-vuln` flags from CI. `ruff` 0,
`bandit` 0, mypy clean over the 33-file allowlist, `pip-audit` exit 0; targeted
Strix/MCP/share/refresh tests 99 green; full backend suite **1609 passed, 3
skipped, 9 deselected**; `revert_strix.py` **27/27** load-bearing. All
**uncommitted**.)

_Previously: 2026-10-19 (Sentry removed from the codebase entirely -- §9.49.
`sentry-sdk` and every trace of it are gone: the dependency + `uv.lock` entry,
`Settings.SENTRY_DSN`, `main.py`'s init block, `credential_check.py`'s inventory
tuple, the `conftest.py` `SENTRY_DSN` guard, `tests/test_no_sentry_in_tests.py`,
`backend/revert_sentry.py`, `test_batch_c_correctness.py`'s assertion, the
AdminView "Sentry Error Tracking" card, `render.yaml`'s env entry,
`backend/.env` + `.env.example`, and the two docs lines that named it. New Relic
already carried the signal, so **no new code**: `main.py`'s generic
`@app.exception_handler(Exception)` logs `unhandled_internal_server_error` with
`exc_info=True` (structlog → OTLP → New Relic Logs), the HTTP middleware and
`trace_span` call `span.record_exception` (New Relic Traces), and
`nexus_errors_total` is the alertable rate; issue-grouping/first-seen alerts are
an NR alert *policy*, not app code. The `conftest.py` telemetry guards were
renumbered (LangSmith, New Relic, dev-secrets) and the header now explains the
shared position rule; the credential `checked` count is 21, not 22. `ruff` 0,
`bandit` 0, mypy clean; full backend suite **1547 passed, 3 skipped, 9
deselected** (1549 minus the two deleted Sentry-guard tests). All
**uncommitted**.)

_Previously: 2026-10-19 (three upgrade gaps from the shared resources -- §9.48:
structured parsing, a visual PDF fallback and memory supersede, each additive and
fail-open so nothing that worked before can now fail. **Gap 1**: a dependency-free
`services/structured_parse.py` (fence-aware, prose-tolerant, string-aware
balanced-brace scan + Pydantic validation — `bool("false")` is `True`) replaces a
naive `strip("```json")`+`json.loads` at `_run_arq`, the researcher, the planner
and the prompt optimizer, plus `response_format` where the provider honours it
(an optimisation, not the guarantee). **Gap 2**: `services/tools/pdf_vision.py`
renders a scanned / image-only PDF with the already-declared PyMuPDF and reads it
through the existing vision chat path (`completion()`, so it is metered), gated on
`PDF_VISION_ENABLED` + `PDF_VISION_MIN_TEXT_CHARS`, bounded by
`PDF_VISION_MAX_PAGES` and a per-page timeout, and preferred only when it
produced strictly more text; `extract_text` stays sync, a new
`extract_text_with_vision` wraps it. **Gap 3**: supersede in
`services/memory_lifecycle.py` (`partition_new_memories_with_supersede`, a strict
superset of the dedupe partition) keyed only on the possessive-attribute shape
("my X is Y", never "I prefer X"), with the repository deactivating the replaced
rows. New guards: `test_structured_parse.py` (19), `test_pdf_vision.py` (12),
`test_memory_supersede.py` (15); `backend/revert_gaps.py` **7/7 load-bearing**.
`ruff` 0, `bandit` 0, the mypy allowlist is now **32** files and clean; full
backend suite **1549 passed, 3 skipped, 9 deselected**. All **uncommitted**.)

_Previously: 2026-10-18 (the production-image boot fixes -- §9.47: preparing
the Render + Vercel deploy surfaced two latent defects in `backend/Dockerfile`
that no test or CI gate could see, because `release.yml` builds the image and
never runs it. (1) The code imports under two namespaces -- `backend.app.*` plus
a bare `app.*` in five `services/` files (18 statements) -- and the runner
supplied only `PYTHONPATH=/app`; locally the editable install maps `app`, so the
gap was invisible off-image. `PYTHONPATH=/app:/app/backend` fixes it, mirroring
the local dual-object layout rather than inventing one. (2) `greenlet` (required
by SQLModel's asyncio bridge) arrived in dev only via a dev-scoped transitive
edge and no `[asyncio]` extra existed to borrow, so the runtime install lacked
it; declared `greenlet>=3.0.0` in `[project] dependencies` (`uv lock` +2 lines,
already 3.5.5). Proven by *running* the image: `app.openapi()['paths']` = 103 and
a real boot gives `/health` 200 + `/health/ready` 200 with `env: production`,
the Supabase pooler, Redis, Qdrant and the **Postgres** checkpointer. The
lesson: a Dockerfile that builds is not a Dockerfile that runs --
`docker run --entrypoint python ... -c "from backend.app.main import app"` plus
a `/health/ready` probe is the missing gate. Also in this batch: `render.yaml`
(a two-service blueprint, all secrets `sync: false`), a PORT-aware CMD,
`backend/.dockerignore` as a secret-leak fix, and `backend/.env` re-pointed to
the hosted services. All **uncommitted**.)

_Previously: 2026-10-16 (dependency-advisory remediation -- §9.46: both
security gates were red at HEAD, and the correct response differed on each side
— upgrade what has a fix, document only what does not. Backend: `uv lock
--upgrade` plus `mcp` pinned up to **2.3.0** (fastmcp 4.1.0) took **25
advisories to 0**; the two remaining are documented `--ignore-vuln`s in
`ci.yml`/`nightly.yml` (**PYSEC-2026-1325** ecdsa, ES*-only; **PYSEC-2026-4298**
python-jose algorithm confusion, and the app's one decode call pins
`algorithms=[…]`, so it is unreachable). A stale `multidict-6.7.1.dist-info` was
making `pip-audit` report a version the lock no longer installed — read
`uv.lock`, not the audit, to decide whether a version moved. Frontend: `next`
**16.3.4 → 16.4.2** clears a critical RCE and six highs; the last advisory
(`braces` / `GHSA-vfj7-8cjw-p6xm`, **no patched release**, build-time lint-only)
is allowed by a new named gate `frontend/scripts/npm-audit-gate.mjs`
(`npm run audit:gate`, 9 tests) rather than by disabling `npm audit`. Bandit was
red too and is fixed in place (documented bodies over bare `pass`, explicit
`raise` over `assert`) in `services/observability/{metrics,otel,tracing}.py`
and `api/v1/messages.py`. Docs swept: Next 16.4.2 and schema **38** tables
across `AGENTS.md`, `README.md`, `docs/{architecture,deployment-guide,user-guide}.md`
and `frontend/README.md`. Verified after the upgrades: backend **1502 passed, 3
skipped, 9 deselected** (exit 0, unchanged — the MCP bump is integration-clean);
frontend **37 files / 377 tests**; `ruff`/`bandit`/`pip-audit`/`audit:gate` all
exit 0; `tsc` 0; `eslint` 0 errors; `next build` 0. All **uncommitted**.)

_Previously: 2026-10-16 (the OAuth redirect-origin fix -- §9.44: the OAuth
callback redirected to `0.0.0.0`. The frontend (locally and in Docker) is the
Next.js **standalone** server, which binds `0.0.0.0` (`scripts/serve.js`
defaults `HOSTNAME=0.0.0.0`; the Docker runner does the same), and Next builds a
route handler's `request.url` from that bound host — so a successful exchange
answered `Location: http://0.0.0.0:3000/app`, ignoring both the `Host` header
and `x-forwarded-host` (measured). That is a different host from the browser's
`localhost`, so the host-only httpOnly session cookies just set on the response
are never sent: the user lands on `/app` unauthenticated and OAuth looks broken
although every backend call succeeded. Fixed at the source — `lib/auth.ts` gained
`requestOrigin(request)` (origin from `x-forwarded-host`/`Host` +
`x-forwarded-proto`/scheme, falling back to `request.url` only when no header is
present), used by every OAuth-callback redirect; `scripts/serve.js` was left
alone because the Docker deployment binds `0.0.0.0` behind a proxy and the
header is the only correct source in both. Live-verified after `next build` +
restart: default Host → `http://localhost:3000/signin?error=...` (was `0.0.0.0`),
`Host: nexus.example.com` → that host, `x-forwarded-host` + `https` →
`https://app.nexus.com/...`. Two load-bearing tests in `route-handlers.test.ts`
(frontend **36 files / 368 tests**); the 23-check live BFF sweep still passes
23/23. Live OAuth is now verified end to end for Google (§9.44) —
sign-in → password → 2-Step Verification → consent → `/app`, with
`/api/auth/me` returning the account — so the redirect URI is registered and the
consent screen is completable. The STT mic UI is fully wired and needs only
browser microphone permission, not code (§9.45). All **uncommitted**.)

_Previously: 2026-10-16 (the all-signals observability upgrade -- §9.43: New
Relic now receives **all three** signals over OTLP/HTTP, not just logs. A new
`services/observability/otel.py` wires OTLP traces + metrics with one shared
Basic-auth header; `main.py`'s HTTP middleware emits a server span per request
*and* records request latency/throughput/errors into the `MetricsCollector`,
which forwards them to OTel (without it the collector exported zero data points,
measured). The collector is now **built from `docker/otel-collector.Dockerfile`**
(pre-creating `/var/lib/otelcol/storage` uid 10001) with an `otel_storage` named
volume and `basicauth/server` on both OTLP protocols, and its metrics pipeline
takes the `otlp` receiver instead of scraping the app (`METRICS_SCRAPE_TARGET` /
collector `METRICS_TOKEN` removed; `/metrics` stays as a local debug surface +
admin viewer). New guards `tests/test_observability_otel.py` (18) and the
existing collector-boot test (3); `docs/observability-deployment.md` rewritten
and `docs/alerting/alert-rules.yml` notes the OTLP metrics path. Live on this
machine: startup logged all three `otlp_*_wired` lines and, after three `/health`
requests, the collector's debug exporter showed `resource spans: 5, spans: 5`,
`metrics: 2, data points: 2` and log batches of 19–21 records. Remote New Relic
ingest remains the remaining ops step, not code. Full backend suite **1502
passed, 3 skipped, 9 deselected** (exit 0); `ruff` 0; the 30-file mypy allowlist
clean. All **uncommitted**.)

_Previously: 2026-10-16 (the dead-code and per-feature audit -- §9.42: a
per-feature pass found and closed four gaps -- BYOK (`services/byok.py`, a
request-scoped key `ContextVar` resolved per attempt-group), a Projects tab with
its own router/service/migration, `ConversationRepository.update` silently
discarding an explicit `null` so "remove from project" was a no-op (the PATCH
returned 200 carrying the old value), and the sign-in page rendering a 422 as
`[object Object]` -- and removed the symbols an AST reference-count proved dead
(`get_hook_service`, `VECTOR_SIZE`, `get_vector_store`, `IQueryDecomposer`,
`_AsyncClientLike`, three test helpers, the orphan `public/favicon.svg`, and two
unreferenced `core/exceptions.py` classes). Both new defects are
load-bearing-revert-verified; frontend 36 files / 366 tests; `ruff` and `tsc`
clean. OAuth live login and the STT mic UI remain blocked on
credentials/environment, not code. All uncommitted.)_

_Previously: 2026-10-14 (Fast mode is real -- §9.41: the composer's Fast/Deep
toggle had been cosmetic since it was built. `page.tsx` mapped Fast → `normal`,
Deep → `agent`, and the backend read neither, so both ran the identical graph.
Repurposing `normal` was off the table — ten test files use it while asserting
full-graph behaviour — so a **new** mode value, `fast`, is the fast path.
Two edits, each separately necessary: `planner_node` returns before its planning
LLM call in fast mode, and `route_after_planner` routes fast straight to the
**synthesizer**, dropping ToT, the orchestrator classifier, subagents, tools and
the retrieval/critic grader. Fast keeps long-term memory (loaded before the
early-return) but does no RAG, so it cites nothing — the intended trade. The
mapping moved to the pure `resolveConversationMode` (`lib/chatMode.ts`, Code >
Web > Fast/Deep) and the hook's duplicated mode unions now reference the single
`ConversationMode` type. New guards: `tests/test_fast_mode.py` (11, reads the
planner branch off the builder) and `src/test/lib/chat-mode.test.ts` (6), with
`revert_fast_mode.py` **4/4** load-bearing. Verified: full backend suite
**1442 passed, 4 skipped, 9 deselected**, frontend **34 files / 352 tests**,
`tsc` 0, `ruff` 0, `eslint` 0. All **uncommitted**.)

_Previously: 2026-10-14 (the STT fallback + landing-page model list -- §9.40:
two fixes from auditing the plain question "is the frontend showing all the
models correctly, and are they right for their work?". The picker was right; STT
had no backup and one public page was lying. `/audio/transcribe` called
`litellm.atranscription` with `groq/whisper-large-v3` and nothing else, and
`atranscription` has **no router fallback**, so the Groq `403`-at-the-egress that
chat survives (§9.39) took speech-to-text down on its own while everything else
kept working. `LiteLLMService.transcribe_audio` now retries the *same audio* on
Gemini through the multimodal chat path (a `file` content part → inline base64
`data:audio/webm`), **rewinding** the `BytesIO` the failed primary already
consumed (without it, "Groq failed, Gemini succeeded" returns an empty
transcript), and raising loudly if `GEMINI_API_KEY` is missing;
`STT_FALLBACK_ENABLED=false` restores the previous behaviour exactly. The landing
page's `MODELS` was a stale second source of truth — it advertised a removed
`groq/compound-mini` and a slug that never existed, `nvidia/nemotron-3:free` —
and now imports `ROUTING_TIERS` from `src/lib/models.ts`. The transcript's model
label also gained its Gemini entries (`gemini-3.5-flash` → "Gemini 3.5 Flash
(vision)", `gemini-3.5-flash-lite` → "Gemini 3.5 Flash Lite (fallback)") so a
Groq-blocked turn no longer shows the raw slug. New guards:
`tests/test_audio_transcription_fallback.py` (7) and
`src/test/lib/models.test.ts` (7); live STT Groq `403 Access denied` → Gemini
transcript `'Please confirm the deployment window is Friday at 3:00 in the
afternoon.'`. Verified: `ruff` 0, mypy allowlist (29 files) clean, the new
backend test 7 passed and 78 targeted regression tests passed, frontend **33
files / 346 tests**, `tsc` 0, `eslint` 0 errors, `next build` 0. The full
backend suite was not re-measured this round. All **uncommitted**.)

_Previously: 2026-10-14 (the free model surface -- §9.39: the whole zero-cost
capability set was audited and made to work end to end. Chat/model-picker, TTS
(`edge-tts`, key-less), STT (Groq), vision and image generation (Pollinations,
key-less) are all free; a `model_info` frame reports the model that *actually*
answered so a provider fallback is visible instead of hidden behind the picker's
label, and `resolve_answer_model` whitelists the two selectable groups. The new
finding was that **both** free text layers can fail at once — Groq `403` under
ProtonVPN (an egress block, not a key: a key is `401`) and OpenRouter
`429 free-models-per-day` (account-wide), while the larger Gemini slug
`gemini-3.5-flash` returned `503` — leaving no reachable group. Added the
`gemini_free` group (`gemini/gemini-3.5-flash-lite`, the one free model that kept
answering) as the last real fallback before the 2.6B model, wired into
`router.fallbacks` and `_fallback_groups`. Also added the missing `GEMINI_API_KEY`
to `backend/.env.example` and corrected the stale `config.py` comment. Live:
`complex_reasoning`/`fast_chat` → `resolved='gemini-3.5-flash-lite'`,
`vision_analysis` → `'Blue'` for a blue image. Full backend suite **1424 passed,
4 skipped, 9 deselected**; `ruff` 0; the mypy allowlist is now **29** files (the
`llm_text_cleanup.py` entry was already on disk but §2/§6 still
said 28 — corrected); frontend 32 files / 339 tests, `tsc` 0, `eslint` 0. All
**uncommitted**.)

_Previously: 2026-10-14 (the UI single-accent + brand mark pass -- §9.38: at the
product owner's request the design system collapsed to **one** accent. The
cyan/violet/emerald `data-accent` swaps, the supporting `--accent-violet/cyan/
lavender/pulse-green` tokens, the Appearance accent picker and the
command-palette accent entries are all gone; `theme.ts` lost
`Accent`/`ACCENTS`/`setAccent`/`ACCENT_KEY` and `useTheme()` is now
`{ mode, resolved, setMode }`, persisting only `nexus-theme`. The app also got a
single brand mark — a lime tile carrying a bold "N" whose diagonal is a chain of
nodes (`components/NexusMark.tsx` for the chat avatar and landing navbar;
`public/nexus-logo.svg`, `src/app/icon.svg` and the regenerated multi-size
`src/app/favicon.ico` for the sidebar/sign-in and the browser tab), replacing the
lucide `Bot` and the old purple→cyan tile. Two chat elements moved to their
correct place: the artifact card is now **below** its bubble and the critic
verdict / evidence block is now **below** the transcript. Frontend-only, all
**uncommitted**; `tsc` 0, `eslint` 0 errors, `next build` 0, Vitest 32 files /
335 tests, live DOM-verified.)

_Previously: 2026-10-14 (the HITL resolve path -- §9.37: the browser Approve
click was failing twice over. The BFF `/api/hitl` route aborted at a hardcoded
30s while the backend was still resuming the parked graph **inline**, so the
client showed a synthetic `HITL feedback failed: 503` and the backend kept
going; the ceiling is now the named `HITL_RESUME_TIMEOUT_MS = 150_000`, verified
live (the same click now surfaces the backend's own status). And a *failed*
resolve collapsed the approval card, because `useNexusChat.resolveHITL`
swallowed the error and `ChatArea`'s failure guard was therefore dead code —
`setResolved` ran on a request the backend never recorded. Fixed by re-throwing
(the card retires only when the decision landed), plus a `Working…` spinner for
the tens-of-seconds wait. Both new tests load-bearing. Live-verified **end to
end**: with the machine's VPN off (it had been routing Groq — the router's
primary — to "Access denied", leaving only an already-exhausted OpenRouter free
quota), the approved `execute_python` runs (E2B 201/200/204), the graph reaches
the `synthesizer`, and the resumed answer appears in the browser. Frontend
**32 files / 335 tests**; `tsc` 0; `eslint` 0.)

_Previously: 2026-10-14 (the live HITL walk -- §9.36: with a provider back
(the OpenRouter free quota reset at 00:00 UTC), the §9.35 walk finally ran end
to end — a real `hitl_request` frame carrying the coder's `os.system` code,
`POST /hitl` resuming with `{"status":"resumed","action":"approve"}`, and the
resumed answer persisted. It exposed a second defect: the streaming fallback in
`iter_graph_frames` had no role check, so an interrupted run — which pauses
before the synthesizer and never sets `_streamed_tokens` — streamed `bootstrap`'s
state delta, whose last message is the *user's* `HumanMessage`, back as the
assistant reply (an assistant row repeating the prompt, observed live). Fixed
with the `_is_assistant_output` denylist of input roles
(`human`/`user`/`system`/`tool`/`function`); two role tests in
`test_run_rejoin.py`, load-bearing. normal-mode reachability is real but
model-dependent: the router is an LLM classifier and `temperature=0.0` on a
hosted model is not determinism; `mode="code"` dispatches the coder reliably.
Backend **1390 passed, 4 skipped, 9 deselected**; `ruff` 0.)

_Previously: 2026-10-13 (the HITL repair -- §9.35: the human-in-the-loop
approval card the user had never seen. Two real breaks, not three: the trigger
was unreachable (`execute_python` ran automatically and nothing ever produced a
pending tool call for `tool_node` to gate), and approving resumed the graph
*inline* off the SSE path so the client received nothing. Fixed with the sync
`requires_approval(tool_name, arguments)` ladder in `tool_gateway.py` (mode via
`check_permission` **and** the previously-dead `requires_approval_if` conditions
in `permissions.yaml`), coder deferral (`status="requires_approval"`), the
dispatcher queue branch, `_post_run_frames` surfacing `snapshot.tasks[].interrupts`
as a real `hitl_request` frame, and `resolveHITL` reloading history after the
decision so the resumed answer is visible. Three §2 seams got their first tests:
the executor frame, the BFF `hitl_request` translation, and the `ChatArea`
card. The third candidate root cause -- a missing resolve route -- was a false
premise and is retracted: `frontend/src/app/api/hitl/route.ts` always existed.
Live E2E could not be completed because every configured provider was down at
the time (Groq network-denied, OpenRouter free daily cap, Gemini models retired
/ 503, OpenAI/Anthropic keys empty); the chain is covered by tests instead.
Backend **1388 passed, 4 skipped, 9 deselected**; frontend **32 files / 333
tests passed**; `tsc` 0; `ruff` 0; `bandit` exit 0.)

_Previously: 2026-10-13 (the prompt-surface rewrite -- §9.34: every builtin
prompt was re-reviewed and rewritten -- all six `prompt_templates/*.txt` (two
data bugs fixed: a mojibake apostrophe in `feedback_prompt.txt` and a stray
`\x07` control char in `custom_user_intent_prompt.txt`), the coder/critic/
researcher agent prompts, the ToT prompts ("logical soundess" typo), the ARQ
template whose prompt string carried the repo's baked-in `â€”` mojibake,
`_DEFAULT_SYSTEM_PROMPT` synced to `system_prompt.txt`, the three RAG judges
(anchored semantics + the §9.21 instruction-neutrality clause), the four
frontend `PROMPT_TEMPLATES` and the two MCP prompt resources aligned to one
voice. `summary_prompt.txt` became load-bearing: the context compiler now loads
it instead of a hardcoded "concise conversation summarizer" string. The three
skills guides were corrected against reality (`file_system` and `document_search`
don't exist; `task_contracts.yaml` was deleted months ago). `PROMPT_SET_VERSION`
bumped to `2026-10-13`. The load-bearing contract prompts (decision, HyDE,
artifact classifier, planner) were deliberately left untouched. Full backend
suite **1381 passed, 4 skipped, 9 deselected**; frontend **328 passed**;
`tsc` and `ruff` clean.)

_Previously: 2026-10-13 (the local-dev quality batch -- §9.33: a live answer
came back as a raw `<|tool_call_start|>[google(...)]<|tool_call_end|>` tool
call because the free-tier fallback chain degraded onto the 2.6B
`liquid/lfm-2.5-2.6b:free`, which writes its tool call on the text channel;
fixed at the single content boundary by `strip_model_artifacts` and by trying
the 30B `vision_analysis` model first, plus a prompt line forbidding the shape.
Same batch: the synthesizer now drips the final guarded `response_text` as
`text_delta` frames (`stream_mode="custom"`, string form), the frontend shows a
Thinking…/Writing… activity chip, the stream route gained a real
`rate_limit(...)` dependency (429 instead of silently unthrottled), and the
run-log flush retries are spaced with linear backoff so a transient connection
blip no longer drops a frame. Local Postgres moved to port **55432** (a native
`postgres.exe` shadows 5432), Qdrant bumped to **v1.19.0** to match the pinned
client, and the Qdrant healthcheck is now a bash `/dev/tcp` probe. Verified
end to end: E2E stream showed clean prose with 65 progressive `text_delta`
frames and zero dropped frames; the browser showed "Thinking…" then "Writing…"
with mid-word incremental rendering.)

_Previously: 2026-10-13 (the uncommitted OAuth/D3 WIP was audited and
repaired -- §9.31: the OAuth state store's missing `import asyncio` and its
unconsumed shared entry, the hardcoded callback origin, `rag/ingest.py`'s
unreachable chunk loop, the silently-dropped usage row, the red mypy gate at
HEAD (`core/config.py`'s stdlib logger kwargs + missing annotation), and the
`UniqueConstraint`s that had no revision. The unique-constraint revision
`d4e5f6a7b8c9` is the new head (11 revisions), and `test_migration_graph.py`
gained a structural guard that every named `UniqueConstraint` in the models is
created by some revision -- docstrings excluded, so it cannot pass on prose.
`ruff` 0, `bandit` 0 and the 28-file mypy allowlist clean; full-suite counts not
re-measured because Docker was down.)

_Previously: 2026-10-07 (New Relic brought **live end to end** -- the
collector's `processors.resource.attributes` entries gained `action: upsert`,
without which the pinned contrib image refuses to start, and
`otelcol validate` exits 0 on that same broken config, so the guard boots a
real container instead; plus the `tests/conftest.py` `SENTRY_DSN` ordering fix,
whose guard ran too late to beat the settings singleton built during import.
US region; the key is held only in the repo-root `.env`. Suites: backend
**1316 passed, 4 skipped** (the 1314 baseline + the 2 new guards); guards
`revert_otel.py` 2/2, `revert_sentry.py` 2/2 and `revert_nr.py` 2/2. The same
day added the third telemetry guard — `tests/conftest.py` turns New Relic log
export off for pytest, whose faker autouse fixture was shipping 47 records per
run (§9.30) — verified by a full-suite run leaving the collector's batch count
at **43 -> 43, delta 0**. §9.29 and §9.30 record this.)

_Previously: 2026-10-05 (LangSmith tracing brought **live** — the 403 was a
wrong-region default, not a bad credential: `LANGSMITH_ENDPOINT` added to
`.env`/`.env.example`/`config.py`, `LANGSMITH_TRACING=true`, ingest verified end
to end against the AWS deployment, and §9.26's "the key cannot reach the
workspace" diagnosis retracted in three places. Guarded by
`tests/test_langsmith_region.py` + `backend/revert_ls.py`, 4/4 load-bearing.
New Relic still blocked on the ingest key. Suite 1306 passed.)

_Before that: 2026-10-02 (Batches A–D incl. D3 artifacts; then migration-graph
linearization, `/auth/oauth/providers` + sign-in wiring, model remap, Makefile
gates, admin user-erasure endpoint, all three dead config files removed,
guardrail Unicode-evasion fix, `docs/api-reference.md` resync; then the
database-target guard — `.env.example`/config/compose DSNs reconciled and
`alembic upgrade`/`downgrade` made to refuse a non-local host without
`ALLOW_REMOTE_MIGRATIONS`; then LLM-backed HyDE — `IRewriter.rewrite` made
`async`, the fixed template demoted to a mode and a fail-open fallback; then the
typed-decision layer — `services/decision.py` reading real logprobs and
refusing to invent a probability, `services/rag/answer_coverage.py` as its first
consumer, and `complete()` gaining opt-in `logprobs` plumbing; then the
per-node retry/timeout policy — `agents/orchestrator/retry_policy.py`, the
unreachable `set_node_defaults` removed, and §9.22/§9.23 recording the measured
failures of LangGraph's default `retry_on` and the `astream_events`
custom-stream trap; then the composer queue — `useNexusChat` enqueues a send
made during a run and drains it from the run's `finally`, with §9.24 recording
the synchronous-ref requirement; then the deferred work itself — answer coverage
measured and left **off** because no configured model returns logprobs
(§9.20/§9.21), the LangSmith key diagnosed as a 403 project token with the
capture kept locally (§9.26), and §9.25/§9.27 replacing the deferral with the
durable run log: `agent_runs` + `run_events`, the graph moved out of the
`StreamingResponse` generator into a background task, `X-Nexus-Run-Id` as a
response header, and a rejoin that replays rather than resends; then C3 — the
citations path closed at its single source (`citation_payloads`, a real
`citation` frame, and the two message columns that had no writer), tool calls
collected from the frames, `get_messages` switched to the *newest* window, the
page's `loadHistory` deleted so the hook is the transcript's only writer,
`X-Nexus-Message-Id` deciding the duplicate answer by identity, and
`pytest-randomly` surfacing the twelfth `_await` helper's missing loop guard.
§9.28 records it all, including the correction to §9.27's false claim that the
chat view loaded no history).
Regenerate counts (tables/endpoints/tests) from code rather than trusting any
static number here — and verify code-shape claims with `ast`, not regex, since
this repo has CRLF checkouts._
