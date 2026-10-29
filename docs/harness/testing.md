> Part of the repo reference. Moved from `AGENTS.md` §7; the section
> numbering is preserved so existing `§7.N` references still resolve.
> Start at [`AGENTS.md`](../../AGENTS.md) for the session contract.

## 7. Testing inventory (verified counts)

- Backend: **76 unit test files** in `backend/tests/`, **11 integration files**
  in `backend/tests/integration/` and **1 e2e file** in `backend/tests/e2e/`
  (9 tests — verified with `uv run pytest -m e2e --collect-only`, which
  collects exactly those 9 and nothing else; the earlier "3 e2e" in this
  section was wrong). Fakes live in the single module
  `backend/tests/fakes.py` (e.g. `FakeSession`, and — since C2 —
  `FakeRunStore`/`FakeRunSession`, which model the run log's own persistence
  shape: ordered paging, `add_all` batches and an async context manager).
  **A new test file that touches the run log must declare its own `store` /
  `session_factory` fixtures from `tests/fakes.py`**, shadowing conftest's
  real-DB one, or it writes to whatever `DATABASE_URL` names (§9.16).
- Suite total: **1306 passed, 4 skipped, 9 deselected, 0 errors, 0 failures**
  for a plain `cd backend && uv run pytest` with Docker up (measured
  2026-10-05, 386s — the run is I/O-bound on the testcontainer; the previous
  measurement was 1301/598s, and the +5 is exactly
  `tests/test_langsmith_region.py`). The
  `integration` marker is unregistered, so the 11 integration files run by
  default; `addopts` excludes e2e, which is where the 9 deselected come from.
  **Do not read the exit code off a pipeline.** `pytest ... | Select-Object -Last
  N` reports **1** on a fully green run: with `LANGSMITH_TRACING=true` the
  langsmith SDK registers an `atexit` `close_session` that logs through a
  handler whose stream pytest has already closed, and under a pipe that
  `ValueError: I/O operation on closed file` reaches the shell as a non-zero
  status. Redirect to a **file** and read the summary line instead — that is
  how the 1306 above was measured. `make test` writes straight to the console
  and is unaffected, and CI has no `.env`, so tracing is off there.
  **If Docker Desktop is not running those 11 files produce ~113
  `DockerException` setup errors**; that is the environment, not a regression.
  Verify with `docker info` before investigating — and on Windows, *restart* it
  if needed: `Start-Process "$Env:ProgramFiles\Docker\Docker\Docker Desktop.exe"`,
  then `docker start nexus_postgres nexus_redis nexus_qdrant` and wait for the
  health checks to leave `starting`. `test_rate_limit.py::test_app_login_allowed_sets_headers`
  shares the `client` fixture → `override_get_db` → `_testcontainers`, so it
  needs Docker too (despite its own docstring saying otherwise); it fails at
  **fixture setup**, so a Docker error there cannot have been caused by
  anything in `app/`.
- Frontend: **37 Vitest test files** under `frontend/src/test/` (**377
  tests**, measured 2026-10-16) + **6 Playwright specs** in `frontend/e2e/`.
- Batch A–D test files: `test_batch_a_wiring.py`, `test_batch_b_memory.py`,
  `test_batch_c_correctness.py`, `test_batch_d_research.py`,
  `test_batch_d_spend.py`, `test_batch_d3_artifact.py`,
  `test_batch_d3_run_events.py`, `test_memory_lifecycle_schema.py`.
  Run durability/rejoin: `test_run_rejoin.py` (49 tests, groups A–F, and
  `backend/revert_c2.py` reverts each fix against it — see §9.27).
  The route→executor seam has one test of its own in
  `test_batch_a_wiring.py` (`..._runs_the_executor_through_its_own_session_factory`),
  because the two prompt-wiring tests there finish before the background run
  opens a session and so cannot observe it.
  Stream-transport: `test_stream_custom_event_trap.py` (10 tests, §9.23).
  RAG: `test_hyde_generation.py` (31 tests, 15-revert harness — see §9.19).
  C3 (citations + message identity, §9.28): `test_run_citations.py` (16) and
  `test_run_message_identity.py` (9), covered by `backend/revert_c3.py`
  (10/10 load-bearing).
  Frontend C3: `src/test/hooks/chat-rejoin.test.ts` (15),
  `src/test/lib/conversation-history.test.ts` (14) and
  `src/test/chat-stream-rejoin.test.ts` (10), covered by
  `frontend/revert_c3.py` (**17/17** load-bearing).
  LangSmith region (the §9.26 outage): `test_langsmith_region.py` (5),
  covered by `backend/revert_ls.py` (**4/4** load-bearing). It reads the SDK's
  own default endpoint out of `langsmith/utils.py` with `ast` rather than
  hardcoding it, because a hardcoded copy is a second source of truth that
  drifts the moment LangChain renames a host.
- `walkthrough.md` once claimed "82 tests" — that is stale. Current counts: see
  above.
- Backend pyproject: `addopts = -v -m 'not e2e'` — plain `pytest` skips e2e.
- **`pytest-randomly` is installed and active**, which means the suite runs in a
  different order on every invocation and CI is the only place the order is
  pinned. A bare `get_event_loop()` in a sync test is therefore a latent
  order-dependent failure, not a style question: `asyncio.run()` sets the thread
  loop to `None` on exit, so any `_await` helper doing the bare call raises
  `RuntimeError: There is no current event loop in thread 'MainThread'` once a
  preceding test has used `asyncio.run`. Twelve files have that `_await` helper
  and **all but one** carry the `try/except RuntimeError: new_event_loop()`
  guard with a comment naming the failure. The outlier was
  `test_admin_user_deletion.py` — proven pre-existing, not introduced by any
  later work: with only `test_batch_d_spend.py` + that file selected and nothing
  else changed, **6 of 8 seeds failed before the guard and 8 of 8 passed after
  it** (measured 2026-10-04). When adding a twelfth, copy the guard.

**The revert check is the part that matters.** Every batch ships with a script
that applies each fix's inverse to the real source, runs the suite, and
requires it to *fail* — then restores the file. A test that passes with and
without the fix is not testing the fix. Nine genuine defects were found this
way that the fixes alone would have hidden, including an `UnboundLocalError`
where a budget stop mid-revision-loop 500'd the request, and two cases where a
wiring test was only a source-grep and passed even with the call's result
discarded.

**A killed revert run leaves a real source file reverted.** The `finally` that
restores it does not run if the process dies, so the next run fails its own
baseline and looks like a genuine test failure. Snapshot the pristine sources
up front and restore them unconditionally when the baseline is red — then say so
rather than debugging a phantom regression. But a snapshot of an *already
reverted* tree restores the damage instead of undoing it, so the harness now
runs a preflight that reports which reverts it finds already applied and
refuses to touch anything. And never run two harnesses against one tree: two
were live at once here, each reverting files the other was restoring, and the
symptoms (a red baseline, then a red suite, then green, then red again with
different tests) looked like three unrelated bugs instead of one.
