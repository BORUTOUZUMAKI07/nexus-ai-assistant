# Progress Log

<!--
Session state for any coding agent (Codex, Claude Code, Cursor, Freebuff …).
Read this at session START; update it at session END. Nothing updates it
automatically — the repository's agent instructions are what make it load-bearing.

Why a file and not a chat summary: the next session's context window does not
contain this one. Anything that only exists in chat is lost at handoff.
-->

## Current State

- **Last updated:** 2026-10-30
- **Live browser verification (this session, VPN off).** Drove the running app in a
  real Chromium through: sign-in → streamed chat → TTS → thumbs vote → branch →
  Web mode → Knowledge upload → semantic search → document-grounded answer →
  Usage → Settings → Admin → Projects → public share page. Two product defects
  were found and fixed, and one earlier claim was corrected:
  1. **TTS never played.** `next.config.ts` had no `media-src`, so it fell back to
     `default-src 'self'` and Chromium rejected the `blob:` MP3 with
     `MEDIA_ELEMENT_ERROR: Media load rejected by URL safety check` — the request
     returned 200 with valid audio (verified: `decodeAudioData` → 1.92 s @48 kHz)
     and the speaker button silently did nothing. Fixed by adding
     `media-src 'self' blob: data:`; re-tested live: `blob:` → 206, `play-ok`,
     `ended`.
  2. **The ARQ safety flag destroyed document grounding.** A benign question
     ("what is the launch code for Project Halcyon? answer from my documents")
     was over-flagged by the small router model, and the short-circuit routed
     straight to the synthesizer with no retrieval — so the answer truthfully
     said "no document has been provided" while the chunk sat in the index at
     score 1.000. Fixed twice over: the `_ARQ_PROMPT_TEMPLATE` now states that
     reading the user's OWN documents is not a safety concern, and
     `route_after_orchestrator` routes a flagged turn to `critic_grader` so local
     retrieval still runs, while `route_after_critic` keeps blocking the web/code
     tool. Re-tested live: the answer now quotes `ORBIT-77` with
     `GROUNDING SOURCES [1] live-verify.md 100%`.
  3. **Correction to an earlier claim:** the sidebar is NOT silently capped at 50.
     `fetchConversations(1, 50)` is page one, and the list renders a
     `Show more (N remaining)` control that pages further. `thumbs_up` does
     persist to `messages.user_feedback` (verified in the DB), but the toolbar
     shows no selected state and swallows errors — that part stands.

- **Local run note:** `frontend/.env.local` points `BACKEND_URL` at
  `127.0.0.1:8010`, not 8000. Start the backend on **8010** or every BFF call
  answers 503. `backend/.env` targets hosted Supabase/Upstash/Qdrant, so a local
  live check must override `DATABASE_URL`/`REDIS_URL`/`QDRANT_URL` to the Docker
  containers or it writes to production.
- **Earlier entry:** 2026-10-30
- **Last commit:** see `git log -1`. This session added end-to-end coverage for
  the nine journeys that were manual-only, the artifact-card fix they uncovered,
  a document-grounded refusal guard, and a readiness field for the rate-limit
  store — then **removed Celery entirely**: no worker, broker, beat, Flower, event
  publisher or `ASYNC_INDEXING`. Uploads index inline and answer 201 when the
  document is searchable.
- **Working tree:** uncommitted — the two earlier fixes
  (`frontend/next.config.ts`, `backend/app/agents/orchestrator/{nodes,graph}.py`)
  plus this round's model-provenance/settings fix:
  `backend/app/services/run_executor.py`,
  `backend/app/api/v1/messages.py`,
  `backend/app/domain/conversation/{schemas.py,repository.py}`,
  `backend/app/services/audit_service.py`,
  `backend/app/services/conversation_service.py`,
  `backend/app/domain/{user,conversation}/models.py`,
  `backend/app/domain/user/schemas.py`,
  `backend/tests/{test_multimodal_persist.py,integration/test_settings.py}`,
  `frontend/src/lib/{models.ts,queries.ts}`,
  `frontend/src/components/SettingsView.tsx`,
  `frontend/src/app/app/page.tsx`,
  `frontend/src/test/lib/models.test.ts`.
- **Standard startup path:** `./init.sh` (or `make init`) — infra up, deps checked, migrations applied, health reported
- **Standard verification path:** `make check` — ruff + mypy allowlist + backend unit suite + frontend Vitest
- **Clean-state gate:** `make clean-check` — run before every commit / handoff
- **Current highest-priority unfinished feature:** none active (see `feature_list.json`; pick ONE and set it `active`)
- **Upload ceiling:** `MAX_UPLOAD_SIZE_MB=4` (audio 4MB too), because the BFF
  request body is capped at ~4.5MB on Vercel. Enforced in the picker, the BFF and
  the backend; the 413 names the actual size. Mirror any change in
  `frontend/src/lib/upload-limits.ts`.
- **Current blocker:** none locally. Docker is up,
  the local stack is healthy, and both behaviour suites pass. Deployment
  (`F-DEPLOY-001`) is blocked on **credentials only** — see *Blockers*.

### Model selection, settings and admin data — live audit (2026-10-30, later session)

Asked to check the two-model picker vs the three-model settings dropdown, and
whether the numbers are real or stale. Both suspicions were right, in a way that
mattered more than the UI:

1. **Every persisted row was stamped with a configured model, not the one that
   answered.** `run_executor._persist_reply` wrote `settings.DEFAULT_MODEL`
   (`groq/llama-3.3-70b-versatile` — a slug the app no longer routes to) into the
   assistant `messages.model`, the `usage_logs.model` and the cost row/call. Three
   consequences, all observed live: the transcript labelled answers with the wrong
   model, cost was priced against it, and the admin panel aggregated **all 164
   rows** under that one slug (`Model & prompt provenance`, `Per-model/provider
   slices`). Fixed: the stream path now passes the checkpoint's `answer_model`
   (already emitted in the `model_info` frame, so the row and the transcript
   cannot disagree); the failure path reads it from the checkpoint via
   `_run_answer_model`, falling back to `settings.DEFAULT_MODEL` only when no
   model produced text. Proven live: a `fast_chat` run now stores
   `model = qwen/qwen3.8-27b` and adds a real `qwen/qwen3.8-27b` admin slice
   (`1 req · 2482ms`) next to the historical legacy row.
2. **The settings "Default model" was a dead control.** `currentModel` always
   started at `DEFAULT_MODEL_ID`; nothing read `settings.default_model`, so
   changing it changed nothing. Fixed in [`page.tsx`](frontend/src/app/app/page.tsx):
   the chat hydrates the saved default once, and never after the user has picked
   a model (`modelChosenHere`, set by `chooseModel`). Proven live: default set to
   `fast_chat` → fresh sign-in → picker arrives on `fast_chat`.
3. **The third settings option was a legacy slug, not a third model.**
   `user_settings.default_model` defaulted to `llama-3.3-70b-versatile`, and
   `modelDisplayName` has no label for it (measured: it returns the raw string),
   so the select rendered a third entry reading the slug beside the two real
   groups. Added `canonicalModelId()` in [`models.ts`](frontend/src/lib/models.ts)
   (maps the legacy slugs to their router groups, mirroring the backend's
   `resolve_model_group`), used it for the select's value and for hydration, and
   labelled any genuinely unmappable value `(legacy)`. The model default is now
   the canonical group in `UserSettings`, `UserSettingsUpdate` and `Conversation`.
   Proven live: legacy value in the DB → two options, `complex_reasoning`
   selected; unknown value → kept as a labelled extra option, never blank.

Also corrected the `audit_service` comment that claimed "last 24h" over a query
with no time bound.

4. **Two more places held the same legacy default**, found by re-checking instead
   of trusting the first fix: `ConversationCreate.model` and
   `ConversationRepository.create()` both defaulted to
   `llama-3.3-70b-versatile`, which is why a freshly created conversation row
   still advertised it even after `ConversationService` was corrected. Verified
   live: `POST /conversations` now stores `complex_reasoning`.
5. **The non-streaming `/conversations/{id}/messages` endpoint had the same
   provenance bug**, plus a worse one: `message_in.model or settings.DEFAULT_MODEL`
   forwarded any string straight to the router, so an unknown or legacy value
   could drop the request out of the fallback chain. It now resolves through
   `resolve_answer_model()` and records `get_last_resolved_model()` (the model
   that answered). Verified live: `model=fast_chat` stores
   `qwen/qwen3.8-27b`; `model=llama-3.3-70b-versatile` resolves to
   `openai/gpt-oss-120b` and answers 200 instead of failing.

6. **Two more legacy stamps on the streaming path**, found after the above and
   fixed: `stream_conversation` created a conversation with
   `settings.DEFAULT_MODEL`, and the HITL `resume` endpoint persisted its
   assistant turn with the same slug. Both now use
   `resolve_answer_model(body.model)` / `get_last_resolved_model()`. Verified
   live against the running API: `POST /conversations/new/stream` with
   `model=fast_chat` stores `model = fast_chat`; with no model it stores
   `complex_reasoning`. (`settings` is no longer imported there at all.)

**Flaky test fixed, not just observed:**
`tests/test_resilience_guards.py::test_breaker_half_open_probe_recovers_on_success`
slept a fixed 0.06s against a 0.05s breaker timeout. The cause was not load: on
Windows `time.monotonic()` is `GetTickCount64` with ~15.6 ms granularity, so two
reads 60 ms apart can measure 46.8 ms and the breaker still looks open. Both
half-open tests now poll `stats()` (which promotes open→half_open without
consuming the probe) against a bounded deadline, and fail with the observed
state if the transition never happens. 16/16 passed across repeated runs.

**Live verification this round used a fresh incognito session**, because the
existing `/app` tab had stopped hydrating (React props absent from the DOM, mount
effect never ran, `mounted` stuck false). Cause: the disk filled to 0 bytes and
Next logged `failed to write ... build-manifest.json (os error 112)`, leaving
`.next` inconsistent. `rm -rf frontend/.next` + dev-server restart fixed it. This
is a local-environment failure, not a product defect — but it is why an old tab
can look like a broken app.

### Verification status at this entry

Everything below ran, in this order, with Docker up and the stack on the
**local** containers (`nexus_postgres` on 55600, Redis, Qdrant) — not Supabase.

| Check | Result | Notes |
|---|---|---|
| `ruff check app tests` | **pass** | All checks passed |
| `mypy --follow-imports=silent` (CI allowlist) | **pass** | 29 source files, no issues (4 event-publisher files removed) |
| Backend suite | **1687 passed, 3 skipped, 9 deselected** (exit 0) | 3m22s, Docker up |
| `tests/integration/test_settings.py` | **12 passed** (exit 0) | Real Postgres testcontainer — this is the suite that previously could not run; the corrected `complex_reasoning` assertion is now proven, not assumed |
| `tests/test_rate_limit.py` | **11 passed** (exit 0) | Same tier, same container |
| Frontend Vitest | **39 files / 415 tests passed** | 36s |
| `npx playwright test` (e2e) | **37 passed** (exit 0) | 38.7s |
| `scripts/check-arch.sh` | **6 rules hold** across 820 files | Proven load-bearing: 3 violations detected and exit 1 |
| `scripts/clean-state-check.sh` | **pass** | 6 dimensions |
| `scripts/verify-feature.sh` | **14 / 15 passing** | Only `F-DEPLOY-001` (credentials) remains blocked |
| `bash scripts/deploy-preflight.sh` | **static: pass; full: 7 items outstanding** | 5 env names + 2 CLIs; no worker service may be declared |
| `npx tsc --noEmit` | **pass** | exit 0 |
| Harness audit (`audit-harness.sh`) | **28/70 → 51/73**, critical 4/7 → 6/7 | The last critical is a known false negative (see below) |

> **`make` is not installed on this machine.** Every `make X` target below has a
> direct equivalent: `make init` → `bash init.sh`, `make clean-check` →
> `bash scripts/clean-state-check.sh`, `make verify-feature F=<id>` →
> `bash scripts/verify-feature.sh <id>`, `make features` →
> `bash scripts/verify-feature.sh --list`. Install with `choco install make` (or
> run the scripts directly) — the Makefile itself is valid and tested by
> `tests/test_makefile_gates.py`.

## In Progress

Nothing is `active`. The backlog that used to sit in the working tree is now
committed in seven reviewable units (`f9ef1a2` … `f309068`), so the next diff has
a baseline it can be read against.

## Next Steps

1. `bash init.sh`, then `bash scripts/verify-feature.sh --list` to see the
   current state before touching anything.
2. Get Render + Vercel credentials (`VERCEL_TOKEN`, `RENDER_API_KEY`), run
   `bash scripts/deploy-preflight.sh` to see exactly which values are missing,
   and supply them — that is the whole of `F-DEPLOY-001`. Today it reports
   `SECRET_KEY`, `ENCRYPTION_KEY`, `DATABASE_URL`, `CORS_ORIGINS`,
   `APP_PUBLIC_URL` unset plus both CLIs absent.
3. On the deployed origin, `GET /health/ready` now returns `rate_limit_store`:
   `redis` means shared limiting, `in_process` means per-instance. Check it once
   the backend is live — that was previously only visible in logs.
4. Bring `F-DEPLOY-001` to `passing` only through
   `bash scripts/verify-feature.sh F-DEPLOY-001` once the deployed /health and a
   real sign-in on the deployed origin both answer.
5. Then pick the next `state: not_started` item and keep WIP=1. The remaining
   manual-only journeys are listed in `docs/golden-paths.md` — HITL approval,
   OAuth round-trip, 2FA, email verification/reset, MCP client session.

## Blockers

- **Render + Vercel credentials** — the only thing blocking deployment
  (`F-DEPLOY-001`). No CLI or token exists on this machine; everything that can
  be proven without an account is checked by `scripts/deploy-preflight.sh`, and
  it passes today (`PREFLIGHT PASSED`, `--static` mode, exit 0).

## Session Log

### Session 001 — 2026-10-29

- **Goal:** Evaluate `walkinglabs/learn-harness-engineering` against this repo
  and adopt what genuinely helps.
- **Completed:**
  - Measured the repo with the course's own audit script (28/70 components,
    4/7 critical) instead of guessing.
  - Added `init.sh` (startup/health gate), `PROGRESS.md` (this file),
    `feature_list.json` (15 features derived from real routers and suites),
    `scripts/clean-state-check.sh`, `scripts/verify-feature.sh`.
  - Added `make check`, `check-arch`, `clean-check`, `verify-feature`,
    `features`, `vcr`, `harness-audit`, `init`, `e2e`, `frontend-test`.
  - **Second pass:** read the whole English corpus (170 files, 10,273 lines —
    14 lectures, 8 projects, 5 SOPs, the `harness-creator` skill and its 5
    pattern references + 16 gotchas), not just the README.
  - Added `docs/golden-paths.md` (25 real Playwright journeys + the paths that
    only have manual coverage) and `docs/agent-graph.md` (nodes, edges, routing,
    and the accumulate-vs-overwrite state rule).
  - Added a 6th clean-state dimension: no `console.log` / `debugger` /
    `breakpoint()` in added lines **or** in new untracked files — proven
    load-bearing by planting one and watching the gate fail.
  - **Third pass — the AGENTS.md split.** `AGENTS.md` was 3,653 lines; it is now
    a **152-line directory page** plus `docs/harness/{project,verified-facts,
    architecture,database,settings,ci-cd,testing,operations,gotchas,doc-sources}.md`
    (3,597 lines total). Sections were moved by script, not retyped, and
    verified line by line: **0 substantive lines lost** (the 14 that differ are
    the intro and §0 preamble, deliberately rewritten). Section numbers are
    preserved because ~20 files cite `AGENTS.md §9.N` in comments — the router
    table maps each number to its new file.
- **Verification run:** `ruff` clean; the three new shell scripts pass
  `bash -n`; `feature_list.json` parses with 15 features (9 passing);
  `tests/test_makefile_gates.py` 9 passed; `clean-state-check` exit 0 and exit 1
  with a planted debug artifact. Full suites not run — Docker was down.
- **Evidence captured:** the audit summary (28/70) and the command outputs
  above.
- **Commits:** none (the working tree was already dirty before this session;
  nothing was committed to avoid mixing these additions with prior work).
- **Files updated:** `init.sh`, `PROGRESS.md`, `feature_list.json`, `Makefile`,
  `scripts/clean-state-check.sh`, `scripts/verify-feature.sh`, `AGENTS.md`.  - **Verification run (third pass):** harness audit **51/73**, critical 6/7;
    `AGENTS.md` 12 internal links, 0 broken; `clean-state-check` exit 0;
    `test_makefile_gates.py` 9 passed; `ruff` clean. Suite still not run.
  - **Known risk / unresolved:**
  - The audit score moved 52 → 51 across the split. That is the heuristic
    reading only the root instruction file; the split cost it one check while
    taking the instruction file from 3,653 lines to 152. Deliberate trade.
  - The audit's "CRITICAL: no dependency lockfile" is a **false negative** —
    `backend/uv.lock` and `frontend/package-lock.json` both exist; the script
    only looks at the repo root. Do not "fix" it by adding a root lockfile.
  - Its "AGENTS.md is 3569 lines (target 50–200)" warning is real. This session
    added a §0 router/contract section rather than attempting a risky split;
    a real split is a separate, reviewable change.
  - `make` is absent from this machine's PATH, so the Makefile targets were
    validated by `tests/test_makefile_gates.py` and by running the underlying
    scripts directly — not by invoking `make`.
  - The full backend suite was **not** run: the Docker daemon was down, so the
    Docker-backed fixtures error out. That is the environment, not a code
    finding — re-run `make test` once Docker is up before trusting a baseline.
- **Next best step:** get Docker up, run `make check`, commit the backlog.

### Session 003 — 2026-10-29 (closing the open items)

- **Goal:** finish the four items session 002 left open: `#6c` worktree
  isolation, the mypy allowlist gap, the Playwright port trap, and the
  uncommitted backlog.
- **Completed:**
  - **#6c worktree isolation, done verifiably.** `scripts/worktree.sh` creates
    and probes real `git worktree` checkouts under `.worktrees/` (gitignored),
    and `tests/test_worktrees.py` proves isolation from git itself rather than
    from a string compare — the first version compared a Windows path against a
    POSIX probe path and failed for the wrong reason. *3 passed.*
  - **Type layer covers the changed code.** `nodes.py` and
    `context_compiler.py` are on the allowlist via the new `root:` prefix, which
    exists because they import as `backend.app.…` and checking them from
    `backend/` makes mypy see one file under two module names. 33 modules at the
    backend root + 2 at the repo root; **clean**. Two genuine typing bugs were
    fixed on the way (a vision payload typed as `str` when it can be a block
    list, and a provider return that could be `None`).
  - **Playwright cannot test a stranger's app any more.** The config pins
    `-p 3000` (so a busy port fails fast instead of drifting) and sets
    `reuseExistingServer: false`. The old local default adopted *whatever* was
    listening on 3000 — this machine runs other projects there, so a run could
    drive another product's UI and report it as Nexus. `tests/test_e2e_harness.py`
    keeps both properties and fails if the spec directory goes empty.
  - **Deploy reduced to one command.** `scripts/deploy-preflight.sh` checks the
    Render blueprint (parses, `nexus-backend` present, free-tier web services
    only, no worker), the Vercel shape, that the deployment contract is
    documented, and — without `--static` — the required env vars (names only,
    never values) and CLI/token presence. `tests/test_deploy_preflight.py` shows
    it fails when a service is not free-tier.
  - **Both gates stopped lying about `uv`.** Run from a pytest subprocess the
    scripts hit `uv: command not found` and reported a false failure; they now
    resolve a venv interpreter first and fall back to `uv`.
- **Verification run:** `ruff check app tests` clean · `scripts/typecheck.sh`
  33 + 2 modules clean · backend **1539 passed, 3 skipped, 159 deselected**
  (2m38s) · frontend **39 files / 409 tests passed** · `test_worktrees.py`,
  `test_deploy_preflight.py`, `test_e2e_harness.py`, `test_makefile_gates.py`
  green · `deploy-preflight.sh --static` exit 0.
- **Not done, deliberately:** the deploy itself. No Render/Vercel credentials
  exist here, and a deploy is not something to fake. Everything provable
  locally is checked; the remaining work is pasting values.
- **Container note:** the four project containers (`nexus_postgres` :55600,
  `nexus_redis`, `nexus_qdrant`, `nexus_otel_collector`) are up. They are the
  user's dev infrastructure, left running on purpose; `docker compose down`
  stops them and no other project's containers were started, used or stopped.



- **Goal:** finish the two remaining upgrades (agent-oriented failures, product
  items) and run the Docker-dependent verification that session 001 could not.
- **Completed:**
  - **Agent-oriented failures (#5):** `.harness/arch-rules.json` (6 rules, each
    with WHAT/WHY/FIX) + `scripts/check-arch.sh` + `make arch-rules`;
    `test_arch_rules.py` keeps the registry parseable, unique, path-valid and
    green so a new rule cannot ship without repair text.
  - **Compaction funnel (#6b):** `ContextCompiler.stage_overflow` — trim
    deterministically, replay the trimmed set when it fits, summarise at most 40
    messages, and report the drop count so the prompt says what was omitted.
    `nodes.py` now uses it instead of summarising all overflow every turn.
  - **Prompt snapshot (#6a):** the synthesizer returns `prompt_snapshot`
    (full assembly, system-prompt sha256, capped at 20k chars, `truncated`
    flag), so it is checkpointed and a run can be explained from evidence.
  - **Verified features:** the gate moved F-FILES-001, F-ADMIN-001,
    F-VOICE-001, F-PLAN-001 and F-ORG-001 to `passing` with recorded evidence.
- **Verification run:** the table above. Reverts: bypassing the funnel → 1 test
  fails; not returning the snapshot → 1 test fails; both restored and re-run.
- **Known risk / unresolved:**
  - **#6c worktree isolation was not done.** It is a dev-process change (parallel
    agents on separate checkouts), not product code, and nothing in CI can prove
    it — claiming it as "upgraded" would be a claim without evidence.
  - `nodes.py` and `context_compiler.py` are **not** in the mypy allowlist, so
    the type layer does not cover this session's code. Adding them is a
    separate, small change (they may surface pre-existing strict errors).
  - The e2e run needs `next dev` on exactly port 3000: Next 16 auto-shifts to a
    random port when 3000 is busy, and Playwright then waits on 3000 until it
    times out. **Resolved in session 003** — the config pins `-p 3000` and sets
    `reuseExistingServer: false`, so a busy port now fails fast instead of
    driving another project's UI.
- **Next best step:** commit the backlog in coherent units; then either add the
  two changed modules to the mypy allowlist or start `F-DEPLOY-001`.
