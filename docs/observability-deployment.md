# Observability Deployment Checklist (Layer-2 collector → New Relic)

> Status: **live locally; the remote deploy is the remaining ops step.** New
> Relic is wired end to end on this machine — the app exports **all three
> signals (logs, metrics, traces)** over OTLP/HTTP to the Layer-2 collector, and
> the collector forwards them to New Relic. The app never touches a New Relic
> key. Every claim below was verified against
> `docker/otel-collector-config.yaml`, `docker/otel-collector.Dockerfile`,
> `docker-compose.yml`, `render.yaml`, and `backend/app/core/config.py`.

## 0. What ships where (three layers)

| Layer | Component | Owns |
|---|---|---|
| L1 | the app | OTLP **logs + metrics + traces** (`services/observability/otel.py`, `logs.py`, `metrics.py`), plus a local `/metrics` debug surface |
| L2 | OpenTelemetry Collector | receives OTLP, tail-samples traces, batches, buffers to disk, forwards |
| L3 | New Relic | the sink; receives the OTLP export from L2 |

LangSmith is a separate concern: it is the **LLM/agent layer** (node I/O,
prompts, token counts, the graph span tree). New Relic is the **infra/ops
layer** (HTTP latency/throughput/errors, app logs, alerting). Both are wired;
neither replaces the other. The app holds the collector's address and the OTLP
credentials, but *not* the New Relic ingest key — that lives only on the
collector (`${env:NEW_RELIC_LICENSE_KEY}`).

## 1. Stand up the Layer-2 collector

Pick ONE of:

**(a) Local dev (docker compose):**

```powershell
# from the repo root; or put these in the gitignored root .env that compose reads
$env:NEW_RELIC_LICENSE_KEY = "xxxx"
$env:OTLP_AUTH_USER = "nexus"; $env:OTLP_AUTH_PASSWORD = "a-real-secret"
docker compose up -d --build otel-collector
```

- Config: `docker/otel-collector-config.yaml` (mounted read-only).
- The collector is **built from `docker/otel-collector.Dockerfile`**, not pulled
  from upstream (compose `build:` + `image: nexus-otel-collector:local`). The
  file pre-creates `/var/lib/otelcol/storage` owned by the collector uid
  (10001) so the fresh `otel_storage` named volume inherits that ownership.
  Against the upstream image the volume is root-owned and the collector exits at
  startup with `open .../exporter_otlphttp_newrelic_logs: permission denied`.
- Because of that, **after changing the config, rebuild and drop the old
  volume**:

  ```powershell
  docker compose rm -sf otel-collector
  docker volume rm nexusai-assistant_otel_storage
  docker compose build otel-collector
  docker compose up -d otel-collector
  ```

- Health: `curl http://localhost:13133/healthz` → `{"status":"Server ready"}`;
  readiness marker in the logs is `Everything is ready`. Self-metrics on `:8888`.
- `otelcol validate` exits **0** on a config that dies at startup (it only
  unmarshals; it never builds the component graph). Always boot the real
  container — that is what `tests/test_otel_collector_config.py` does.

**(b) Render (free tier):** deploy the blueprint in `render.yaml`
(rootDir `docker`, `otel-collector.Dockerfile`, `PORT=4318`). Render forwards
public HTTPS :443 → :4318. No Render health check is configured (the agent's
`/healthz` is on :13133, unreachable through the single public port).

## 2. Set the collector secrets (Render: `sync: false`, prompt at deploy)

- `NEW_RELIC_LICENSE_KEY` — **required**. New Relic ingest key (collector-side
  only; never in the app env or a tracked file).
- `ENVIRONMENT` — `production` / `development` (becomes the
  `deployment.environment` resource attribute).
- `SERVICE_VERSION` — e.g. `1.0.0` (becomes `service.version`; app default
  `dev`).
- `OTLP_AUTH_USER` / `OTLP_AUTH_PASSWORD` — **required**. HTTP basic auth for
  the OTLP receiver; the app must send the same pair. Change the `nexus`/`nexus`
  placeholder in production.

There is no `METRICS_SCRAPE_TARGET` / `METRICS_TOKEN` any more: metrics arrive
over **OTLP push** from every app process, not from a Prometheus scrape of one
process. (Scraping one process under-reports under multiple workers; and a
public `/metrics` is an unnecessary exposure.)

## 3. Point the app at the collector

In the app's env (`.env` / Render app service):

```
NEW_RELIC_ENABLED=true
NEW_RELIC_OTLP_ENDPOINT=http://localhost:4318                 # local: the compose collector
NEW_RELIC_OTLP_ENDPOINT=https://your-collector.onrender.com    # prod: your Render collector
NEW_RELIC_EXPORT_INTERVAL_SECONDS=30                           # default, optional
OTLP_AUTH_USER=nexus                                           # must match the collector
OTLP_AUTH_PASSWORD=nexus
SERVICE_VERSION=dev                                            # prod: the release tag
```

- Requires the `[observability]` extras installed (`uv sync --extra observability`).
- Export is fail-open and env-gated; a wrong endpoint is dropped telemetry,
  never a crash. The emitter gets one shared `Authorization: Basic <base64>`
  header — logs, metrics and traces authenticate identically.
- All three signals (logs, metrics, traces) are wired from `core/logging.py`
  under `NEW_RELIC_ENABLED`. Traces also include an HTTP server span per request
  and a span per graph node (`services/observability/tracing.py`).

## 4. `/metrics` is now a local debug surface, not the export path

Metrics leave the app over OTLP. The old Prometheus `/metrics` endpoint is kept
for local inspection and the admin viewer; it can still be guarded:

- App: `METRICS_TOKEN=<secret>` → scrapers must send
  `Authorization: Bearer <secret>` (enforced in `api/v1/metrics.py`).
- The collector no longer scrapes it, so there is nothing to configure on the
  collector side. `docs/alerting/alert-rules.yml` and `docs/slo.md` still
  describe the metric families (`nexus_requests_total`, `nexus_errors_total`,
  `nexus_latency_seconds`) — they now arrive at New Relic over OTLP with the
  same names.

## 5. Verify

- Collector: `curl http://localhost:13133/healthz` → `{"status":"Server ready"}`;
  watch its logs for `otlphttp/newrelic` export failures/429s (a free-tier
  quota hit is by design; tail sampling keeps NR spend low — 100% of error
  spans, 10% of the rest).
- OTLP receiver auth: `POST http://localhost:4318/v1/logs` **without** an
  `Authorization` header → `401`; with `Authorization: Basic base64(nexus:nexus)`
  → `200`.
- App startup, with `NEW_RELIC_ENABLED=true`:

  ```
  otlp_log_handler_wired    endpoint=http://localhost:4318/v1/logs
  otlp_trace_provider_wired endpoint=http://localhost:4318/v1/traces
  otlp_meter_provider_wired endpoint=http://localhost:4318/v1/metrics
  ```

- Signals arriving at the collector (its `debug` exporter prints counts):
  `Logs {"resource logs": 1, "log records": N}`,
  `Traces {"resource spans": 2, "spans": 2}`,
  `Metrics {"resource metrics": …, "data points": …}`.
- New Relic: check the `Logs`, `Metrics` and `Traces` (distributed tracing)
  tabs.

### 5a. Already verified without a remote deploy (2026-10-16)

New Relic ingest requires a key this environment does not own for the *remote*
deploy, but every local hop was measured end to end:

| Check | Result |
|---|---|
| collector boots from the Dockerfile | yes — `/healthz` `{"status":"Server ready"}`, `Everything is ready` |
| OTLP auth enforced | `POST /v1/logs` no auth → `401`; `Basic nexus:nexus` → `200` |
| logs reach L2 | debug exporter: `log records: 19–21` per batch |
| traces reach L2 | debug exporter: `resource spans: 2, spans: 2` (HTTP server spans) |
| metrics reach L2 | debug exporter: `data points: …` (HTTP request latency/throughput/errors) |
| app wiring | `otlp_log_handler_wired` + `otlp_trace_provider_wired` + `otlp_meter_provider_wired` on startup |

**Not verified from here:** the New Relic `Logs`/`Metrics`/`Traces` tabs, the
free-tier quota behaviour, and sampling. Those need step 1–3 done on an account
this environment has no access to.

One thing to know when you run this yourself: the log wiring lives inside
`setup_logging()` (`core/logging.py`), **not** at module import. A script that
imports `core.logging` and inspects `logging.getLogger().handlers` without
calling `setup_logging()` sees zero OTLP handlers and concludes the feature is
broken. `main.py` calls it at import, so the real app is fine. Under pytest,
`tests/conftest.py` forces `NEW_RELIC_ENABLED=false` **before** the settings
singleton is built, so the OTel surface is a strict no-op in the suite.
