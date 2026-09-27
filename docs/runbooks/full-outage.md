# Full outage

Alert: `NexusRequestsDown` (scrape `up == 0` for 2m) — worst case, users report
a dead app.

## Symptoms
- `/metrics` scrape failing (`up{job="nexus-backend"} == 0`).
- Smoke check `/api/v1/health` returns 5xx or times out.
- Possibly total loss of traffic (frontend errors on every request).

## Initial triage (5 minutes)
1. **Is it deployed at all?** `kubectl get pods` / `docker compose ps`. Confirm
   the backend container is `Up` and not CrashLoopBackOff.
2. **Was there a recent deploy?** Check the last image tag vs. the previous
   known-good tag. If a rollback exists for `main`, roll back now and keep
   diagnosing after traffic is restored.
3. **Is it a loop/factory problem on Windows/dev?** Dev boxes must start uvicorn
   with the selector event loop (`--loop
   backend.app.infrastructure.common.event_loop:event_loop_factory`); a
   ProactorEventLoop crash leaves the app half-started. Confirm startup logs
   show `nexus_startup_complete`.
4. **Lifespan dependency failed?** A failing `init_db` / `lifespan_graph`
   startup step aborts the whole app. `docker logs <container> | tail -100` and
   look for `nexus_startup_failed` / a traceback in:
   `backend/app/main.py` lifespan.

## Diagnosis
- Read `/metrics` scrape errors first — a failed scrape says **Prometheus
  cannot reach the pod**, which is usually network/CI, not code.
- Then read `nexus_ai_shutting_down` / `unhandled_exception_handler` logs —
  an unhandled exception inside a request turns into a 500 but shouldn't take
  the process down; a panic in a lifecycle worker (`lifespan_graph`) can.
- Check until processes see a full botch: `graceful_shutdown_wait` timeouts can
  leave blocked connections behind a rolling restart. Confirm pool drain logs.

## Resolution
- **Rollback first.** Revert to the previous known-good build.
- **Database schema drift:** if a new migration (e.g. `backend/migrations`)
  isn't applied, `init_db` may fail against prod. Apply the migration head:
  `alembic upgrade head` before restarting.
- **Secrets:** `.env` present? `JWT_SECRET_KEY` / `DATABASE_URL` / Redis URL
  valid? A secret resolution failure aborts startup.

## Prevention
- Migration head check in CI (add to the deployment pipeline).
- Smoke-test `/api/v1/health` + `/metrics` for 60s in the canary job (see
  `.github/workflows/ci.yml`).
- Keep runbooks updated: this page should be < 5 minutes to first mitigation.