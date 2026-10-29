# AGENTS.md — Nexus AI Assistant Repo Context

**What this is:** a full-stack AI copilot — FastAPI + LangGraph multi-agent backend
(DDD modular monolith, PostgreSQL/Qdrant/Redis/Celery) and a Next.js 16 React
frontend that reaches the backend only through its own BFF route handlers.

This file is a **directory page**. It carries the session contract and points at
the reference material. The detail lives in `docs/harness/` because a 3,600-line
instruction file is not read — it is skimmed. Open the topic file when the task
touches that area.

If anything here contradicts the code, **trust the code** and update this file.

---

## 0. Session contract (read this first)

An agent session is governed by four small artifacts:

| Artifact | Answers | Read it |
|---|---|---|
| `PROGRESS.md` | What is verified right now, what broke, what is next | at session **start**, update at **end** |
| `feature_list.json` | What work exists and what counts as proof for each item | when choosing work |
| `init.sh` | Is the environment healthy before I change anything? | before the first edit |
| `scripts/clean-state-check.sh` | Is the repo safe to hand off? | before every commit |

### Clock-in — startup (do not skip)

1. `./init.sh` — infra up, deps present, migrations applied, health printed.
   A failing baseline is fixed **first**; never stack feature work on it.
2. Read `PROGRESS.md` → *Current State* and *Next Steps*.
3. Read `feature_list.json` and pick **exactly one** unfinished feature.
4. `git log --oneline -5` and `git status` — know what is already in flight.

### Clock-out — handoff (do not skip)

1. `make check` — ruff + mypy allowlist + backend unit + frontend Vitest.
2. `make verify-feature F=<id>` — the **only** way a feature becomes `passing`.
   Never hand-edit `state` in `feature_list.json`.
3. `make clean-check`.
4. Update `PROGRESS.md` (current state, what ran, what is still unverified).
5. Commit in reviewable units, message explaining **why**, not what.

### Rules this repo enforces

- **WIP=1.** One `active` feature at a time; *clean-state-check* fails on two.
- **Definitions of done are executable.** "Implemented" is not done; a command
  that ran and passed is done. Never weaken an assertion to make a gate green.
- **Scope is a boundary, not a suggestion.** Fix the selected feature. A blocker
  justifies a narrow supporting fix, and it gets recorded in `PROGRESS.md`.
- **If you are running low on context, do NOT rush to finish.** Stop, update
  `PROGRESS.md`, commit a clean checkpoint. A rushed finish costs the next
  session more than an unfinished feature does.
- **Reality is measured, never recalled.** Test counts, coverage, "it works" —
  re-run the command. A number in a doc is a claim about the past.
- **Never skip a verification layer** (below), and never refactor before the
  behaviour you are changing has passed layer 2.
- **One logical operation per commit.** Commit atomicity is what makes a revert
  a revert instead of a surgery. "Reviewable units" means one change with one
  reason, not "whatever is dirty at 6pm".
- **A feature must be completable in one session.** If it cannot be, it is not
  one feature — split it. That rule is why `feature_list.json` has 15 entries
  and not 4.
- **Cleanup has two modes.** Immediate, at the end of every session (that is
  `make clean-check`); and periodic, a full sweep for structural drift that the
  per-session gate cannot see — duplicated helpers, docs that no longer match,
  a growing list of unenforced rules.

### Definition of done (executable)

A change is done when the layers that apply to it ran and passed, in order —
**never skip a layer**:

1. **Static** — `ruff`, `mypy` allowlist, `tsc --noEmit`.
2. **Behaviour** — the unit suites for what you touched (`make check`).
3. **System** — the end-to-end (e2e) journey in `docs/golden-paths.md`
   (`make e2e`) whenever the change crosses a component boundary:
   frontend → BFF route → FastAPI → DB.

A failure at layer N stops the work; do not proceed to layer N+1 and do not
weaken the check to get past it.

---

## 1. Where the detail lives

Section numbers are **stable**: comments and tests across the repo cite them as
`AGENTS.md §9.27`, `§2`, `§7`. Moving a section kept its number.

| § | Topic file | Read it when |
|---|---|---|
| 1 | [`docs/harness/project.md`](docs/harness/project.md) | you need the monorepo layout and stack |
| 2 | [`docs/harness/verified-facts.md`](docs/harness/verified-facts.md) | **before "fixing" anything that looks wrong** — these are deliberate |
| 3 | [`docs/harness/architecture.md`](docs/harness/architecture.md) | adding a module, router, model, or frontend view |
| 4 | [`docs/harness/database.md`](docs/harness/database.md) | touching models, migrations, or the DSN |
| 5 | [`docs/harness/settings.md`](docs/harness/settings.md) | adding a setting, a model group, or a feature flag |
| 6 | [`docs/harness/ci-cd.md`](docs/harness/ci-cd.md) | reproducing a CI failure locally |
| 7 | [`docs/harness/testing.md`](docs/harness/testing.md) | writing tests, or a suite fails for a reason you do not recognise |
| 8 | [`docs/harness/operations.md`](docs/harness/operations.md) | running anything — commands table |
| 9 | [`docs/harness/gotchas.md`](docs/harness/gotchas.md) | **always skim §9 before a fix** — 50 numbered bugs that actually happened |

**Observability of a change:** a run is durable and replayable via `agent_runs`
and `run_events` (one row per SSE frame, ordered by `seq`), and every service
logs structured events with `structlog`. When you cannot explain a behaviour
from runtime evidence, the fix is to emit the missing signal — not to reason
about the code again. There is no separate sprint-contract ritual here: the
contract for a unit of work is its entry in `feature_list.json`, whose layers
are the acceptance criteria.
| 10 | [`docs/harness/doc-sources.md`](docs/harness/doc-sources.md) | you need to know which doc is authoritative for what |

Two further docs are read *while working*, not at startup:

- **[`docs/golden-paths.md`](docs/golden-paths.md)** — every user journey with an
  observable definition of clean and the spec that proves it. Read it before
  claiming a UI path works; "the code looks right" is not evidence.
- **[`docs/agent-graph.md`](docs/agent-graph.md)** — the LangGraph nodes, edges,
  routing rules and, critically, **which state fields accumulate vs. overwrite**.
  Read it before adding a node or a state field.

## 2. Architecture boundaries (enforced, not aspirational)

- Routers never contain business logic; services never import routers.
- A new router must be registered in `backend/app/api/v1/api.py`.
- New models must be imported in **both** `infrastructure/database/engine.py`
  (for `create_all`) and `migrations/env.py` (for autogenerate).
- The browser never calls `localhost:8000`; everything goes through a BFF route
  handler under `frontend/src/app/api/**/route.ts`.
- `make check-arch` runs the invariant suites (`test_behavioral_invariants`,
  `test_rag_architecture`, `test_effect_scan`, `test_wiring`).
- **MCP** is mounted at `/mcp` (the JSON-RPC endpoint is `/mcp/mcp`) and gated by
  a middleware accepting a Bearer JWT, the `nexus_access_token` cookie, or the
  `x-nexus-mcp-key` header. Tool permissions come from `backend/config/permissions.yaml`
  via `tool_gateway.py` — see §3 in `docs/harness/architecture.md`.

**Promotion rule:** when a review finds a new *category* of mistake, it becomes
an executable check in the same session — a rule that lives only in this file is
a rule the next agent will break.

## 3. Working commands

```bash
./init.sh                 # startup gate: infra, deps, migrations, health
make check                # static + behaviour layers
make check-arch           # architectural invariants
make e2e                  # system layer (Playwright golden paths)
make clean-check          # clock-out gate
make verify-feature F=<id>
make test                 # full backend suite (needs Docker)
```

`make` may be absent on Windows; every target has a script equivalent
(`bash init.sh`, `bash scripts/clean-state-check.sh`, …).
