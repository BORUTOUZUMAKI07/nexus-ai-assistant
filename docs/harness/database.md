> Part of the repo reference. Moved from `AGENTS.md` §4; the section
> numbering is preserved so existing `§4.N` references still resolve.
> Start at [`AGENTS.md`](../../AGENTS.md) for the session contract.

## 4. Database

**38 tables** (verified: `SQLModel.metadata.tables` at runtime, 2026-10-16).
Full list:

`agent_runs, api_keys, artifact_versions, artifacts, audit_logs, bandit_rewards,
conversation_branches, conversation_shares, conversations, cost_logs,
evaluation_logs, file_chunks, file_metadata, files, hook_policies,
message_attachments, messages, organization_invites, organization_members,
organizations, plans, projects, prompt_optimization_runs, prompt_templates,
prompt_versions, redteam_runs, run_events, skills, system_configs, tool_calls,
tool_permissions, tools, usage_logs, user_memories, user_settings, users,
webhook_deliveries, webhook_endpoints`

`agent_runs` and `run_events` are the run log added by §9.27 — the pair that
makes a run durable and replayable, and the reason rejoin is a read rather than
a resend.

- ORM: SQLModel (SQLAlchemy under the hood); async via `asyncpg`, sync via
  `psycopg2`/`psycopg3`.
- Migrations: Alembic (`backend/migrations/`, head `4289b5262252` —
  **12** revisions: `0001_initial_schema` → `9290fa24428d` → `a1b2c3d4e5f6` →
  `c1d2e3f4a5b6` → `d3e4f5a6b7c8` → `e5f6a7b8c9d0` → `f6a7b8c9d0e1` (closes a
  pre-existing drift) → `a7b8c9d0e1f2` (row-level security) →
  `b1c2d3e4f5a6` (Batch B memory lifecycle columns) →
  `c2a1b2c3d4e5` (agent_runs + run_events, the durable run log — §9.27) →
  `d4e5f6a7b8c9` (unique constraints on `organization_members` and
  `api_keys` — §9.31) →
  `4289b5262252` (projects table — §9.42)).
  `alembic upgrade head` before first boot.
  **`c2a1b2c3d4e5` has never been applied to the hosted database**, and must not
  be (§9.16/§9.18). It *was* verified offline (`alembic upgrade
  b1c2d3e4f5a6:c2a1b2c3d4e5 --sql`, exit 0) and, on 2026-10-04, **end-to-end from
  an empty database on real Postgres** in a throwaway container — 38 tables,
  `uq_run_events_run_id_seq` a genuine `UNIQUE (run_id, seq)`, RLS on both new
  tables, `alembic_version = c2a1b2c3d4e5` (§9.28). So a test that reaches a real
  session now works **against a local database you migrated**, and still fails
  with `relation "agent_runs" does not exist` against the hosted one — which is
  the correct behaviour, not a bug to work around.
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
    ten revisions fail identically** with `No module named 'backend'` before
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
