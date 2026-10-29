> Part of the repo reference. Moved from `AGENTS.md` §8; the section
> numbering is preserved so existing `§8.N` references still resolve.
> Start at [`AGENTS.md`](../../AGENTS.md) for the session contract.

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
| Standard startup gate | `./init.sh` / `make init` |
| Full verification | `make check` (lint + types + backend unit + frontend unit) |
| Architecture invariants | `make check-arch` |
| Clean-state gate (clock-out) | `make clean-check` |
| Verify one feature | `make verify-feature F=F-CHAT-001`; list: `make features` |
| Verified-completion ratio | `make vcr` |
