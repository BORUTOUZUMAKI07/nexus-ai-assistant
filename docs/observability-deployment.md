# Observability Deployment Checklist (Layer-2 collector → New Relic)

> Status: **pending — deploy later.** The code (3-layer observability) is on
> `main`; bringing New Relic live is an ops task on the free tier. Steps below
> were verified against `docker/otel-collector-config.yaml`, `docker-compose.yml`,
> `render.yaml`, and `backend/app/core/config.py`.

The app never touches a New Relic key. The **collector** owns
`NEW_RELIC_LICENSE_KEY`; the app only needs `NEW_RELIC_ENABLED` +
`NEW_RELIC_OTLP_ENDPOINT`.

## 1. Stand up the Layer-2 collector

Pick ONE of:

**(a) Local dev (docker compose):**

```bash
# from the repo root
$env:NEW_RELIC_LICENSE_KEY = "xxxx"   # PowerShell; or use the .env `docker compose` reads
docker compose up otel-collector
```

- Config: `docker/otel-collector-config.yaml` (mounted read-only).
- `extra_hosts: host.docker.internal:host-gateway` already lets it scrape the
  host app's `/metrics` at `host.docker.internal:8000`.
- Health: `curl http://localhost:13133/healthz`. Self-metrics on `:8888`.

**(b) Render (free tier):** deploy the blueprint in `render.yaml`
(rootDir `docker`, `otel-collector.Dockerfile`, `PORT=4318`). Render forwards
public HTTPS :443 → :4318. No Render health check is configured (the agent's
`/healthz` is on :13133, unreachable through the single public port).

## 2. Set the collector secrets (Render: `sync: false`, prompt at deploy)

- `NEW_RELIC_LICENSE_KEY` — **required**. New Relic ingest key (agent-side only).
- `ENVIRONMENT` — `production` / `development` (becomes a resource attribute).
- `METRICS_SCRAPE_TARGET` — the app's public URL for `/metrics`, e.g.
  `https://nexus-backend.onrender.com` (compose default: `host.docker.internal:8000`).
  Leaving this empty breaks collector startup on purpose (Prometheus refuses an
  empty target).
- `METRICS_TOKEN` — set it **both here and on the app** if you guard `/metrics`
  (see step 4). Empty string is fine for local dev.

## 3. Point the app at the collector

In the app's env (`.env` / Render app service):

```
NEW_RELIC_ENABLED=true
NEW_RELIC_OTLP_ENDPOINT=http://localhost:4318        # local: the compose collector
NEW_RELIC_OTLP_ENDPOINT=https://your-collector.onrender.com   # prod: your Render collector
NEW_RELIC_EXPORT_INTERVAL_SECONDS=30                 # default, optional
```

- Requires the `[observability]` extras installed (`uv sync --extra observability`).
- Export is fail-open and env-gated; wrong endpoint = dropped telemetry, never
  a crash.

## 4. (Optional) Guard `/metrics`

`/metrics` is open by default. To require a bearer token:

- App: `METRICS_TOKEN=<secret>` → scrapers must send
  `Authorization: Bearer <secret>` (enforced in `api/v1/metrics.py`).
- Collector: the prometheus scrape in `docker/otel-collector-config.yaml`
  already sends `authorization: Bearer ${env:METRICS_TOKEN}` — set the same
  value in the collector env.
- Note: the `otlp` receiver on :4318 is PUBLIC on Render's free tier; the
  config's commented `basicauth/server` block is the lock-down if you need it.

## 5. Verify

- Collector: `curl http://localhost:13133/healthz` (local) → `{"status":"Server
  ready"}`; watch its logs for `otlphttp/newrelic` export failures/429s.
- App: startup log `otlp_log_handler_wired endpoint=...` when
  `NEW_RELIC_ENABLED=true`; `/metrics` renders the `nexus_*` families.
- New Relic: check `Logs` and `Metrics` query tabs; errors/429s in collector
  logs mean the free-tier ingest quota was hit (by design, sampling keeps NR
  spend low: 100% of error spans, 10% of the rest).

### 5a. Already verified without the credential (2026-10-04)

The app half of this checklist is **code-complete and measured**, so the only
remaining work is the ops half. Verified with `NEW_RELIC_ENABLED=true` and
`NEW_RELIC_OTLP_ENDPOINT=http://127.0.0.1:4318` (deliberately unreachable):

| Check | Result |
|---|---|
| handler attaches | yes — root handlers `['StreamHandler', 'LoggingHandler']` |
| endpoint | `http://127.0.0.1:4318/v1/logs` (`base` + `/v1/logs`) |
| startup event | `otlp_log_handler_wired` |
| idempotent | second `setup_otlp_log_handler()` returns the same handler; handler count stays 2 |
| fail-open | a log call raised nothing; export failure surfaces as a log line, and `shutdown_otlp_log_handler()` returns cleanly |

**Not verified, and not verifiable from here:** anything requiring the ingest
key or a deployed collector — the actual New Relic `Logs`/`Metrics` tabs, the
free-tier quota behaviour, and sampling. Those need steps 1–4 done on an
account this environment has no access to.

One thing to know when you run this yourself: the wiring lives inside
`setup_logging()` (`core/logging.py:183`), **not** at module import. A script
that imports `core.logging` and inspects `logging.getLogger().handlers` without
calling `setup_logging()` will see zero OTLP handlers and conclude the feature
is broken. `main.py:40` calls it at import, so the real app is fine.