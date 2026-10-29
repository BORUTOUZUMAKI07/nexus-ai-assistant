> Part of the repo reference. Moved from `AGENTS.md` §6; the section
> numbering is preserved so existing `§6.N` references still resolve.
> Start at [`AGENTS.md`](../../AGENTS.md) for the session contract.

## 6. CI / CD (all gates green locally at the D3 commit)

`.github/workflows/`:

- **`ci.yml`** — every push/PR:
  1. `test` job: backend `pytest -m "not e2e"` (unit + behavioral-invariant;
     includes offline arena/conversational/prompt-regression heuristics).
  2. `security` job: bandit over `app/`, pip-audit over the locked venv
     (**no ignores** — the two no-fix advisories `PYSEC-2026-1325` ecdsa and
     `PYSEC-2026-4298` python-jose left the tree when python-jose was replaced
     by PyJWT, §9.50; `--ignore-vuln` was removed from `ci.yml`/`nightly.yml`),
     the npm audit gate (`npm run audit:gate`, a documented no-fix allowlist —
     §9.46), **mypy over the 30-file allowlist**.
  3. `frontend` job: `npx next typegen` → `tsc --noEmit`, eslint, vitest,
     `next build`.
  4. **Live LLM eval gates** — job-level `if: vars.RUN_LLM_EVAL_GATES` (the
     **repository variable**, not a secret): prompt-regression, arena,
     conversational compliance, RAG faithfulness. Uses `OPENROUTER_API_KEY`
     secret. This was the CI breakage fixed: the gate previously used an
     invalid `env.…` expression in `if`.
- **`nightly.yml`** — nightly security scans (bandit + pip-audit), full backend
  suite (unit + integration + e2e), npm audit, and the frontend vitest suite.
- **`release.yml`** — on tags: build Docker image, push to GHCR.
- `concurrency: cancel-in-progress` on the same branch; 25-min `timeout-minutes`
  on the test job.

Run the same things locally:

```bash
cd backend
uv run pytest -m "not e2e"          # or: make test
uv run ruff check app tests migrations scripts
uv run bandit -c pyproject.toml -r app -q
uv run pip-audit
uv run mypy --follow-imports=silent \
  $(grep -v '^\s*#' scripts/mypy_targets.txt | sed 's/\r$//;s|^backend/||')
cd frontend
node "node_modules\vitest\vitest.mjs" run   # Windows: bin shim is broken
npm run audit:gate                          # npm audit + documented allowlist
npx next typegen && npx tsc --noEmit
npx eslint src scripts
```

Windows shell gotchas (learned the hard way):

- PowerShell masks real exit codes — always check `$LASTEXITCODE` after tools.
- Harness scripts should run `sys.stdout.reconfigure(encoding="utf-8",
  errors="replace")` (or equivalent) or emoji/UTF-8 output looks mojibake'd.
- Use `uv run` / Make targets instead of bare `uvicorn`/`alembic` where
  possible; notebooks/scripts importing backend code should add the repo root
  to `sys.path`.
- Use `app.openapi()` (not `app.routes`) for endpoint introspection.
- **`api_router.routes` does not list paths on this FastAPI version.**
  `include_router` leaves `_IncludedRouter` objects that have no `.path`, so
  `{getattr(r, "path", "") for r in api_router.routes}` yields `{""}` and a
  test asserting a route was registered passes for the wrong reason. Use
  `app.openapi()["paths"]`.
