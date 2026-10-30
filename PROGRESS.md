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
- **Last commit:** see `git log -1`. This session added end-to-end coverage for
  the nine journeys that were manual-only, the artifact-card fix they uncovered,
  a document-grounded refusal guard, and a readiness field for the rate-limit
  store — then **removed Celery entirely**: no worker, broker, beat, Flower, event
  publisher or `ASYNC_INDEXING`. Uploads index inline and answer 201 when the
  document is searchable.
- **Working tree:** clean (`git status` empty) as of this entry.
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

### Verification status at this entry

Everything below ran, in this order, with Docker up and the stack on the
**local** containers (`nexus_postgres` on 55600, Redis, Qdrant) — not Supabase.

| Check | Result | Notes |
|---|---|---|
| `ruff check app tests` | **pass** | All checks passed |
| `mypy --follow-imports=silent` (CI allowlist) | **pass** | 29 source files, no issues (4 event-publisher files removed) |
| Backend suite | **1685 passed, 3 skipped, 9 deselected** (exit 0) | 6m15s |
| Frontend Vitest | **39 files / 411 tests passed** | 30s |
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
