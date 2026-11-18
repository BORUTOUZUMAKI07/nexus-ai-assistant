# Runbook: New Relic UI empty while the collector reports successful exports

**When to use:** the Layer-2 collector logs show `logs`, `metrics` and `traces`
batches reaching `otlphttp/newrelic` with no exporter failures, but the New
Relic UI is empty for entity GUID
`ODU5NDM1MnxFWFR8U0VSVklDRXwtNjE0NDUwMTQyNzM1ODM5ODUwMQ` for more than a day.

## Verified status (2026-11-18)

**Account-level ingestion is proven; the entity-scoped metrics panel can still
be empty.** This was verified in the New Relic UI and Render logs, not inferred
from collector config:

- The collector debug exporter emitted metrics, traces and logs through
  `2026-11-18T13:18:52.357Z`. At `13:33:50Z` Render logged
  `Received signal from OS {"signal": "terminated"}`, followed by a clean
  collector shutdown — the expected Free-tier idle-out.
- The entity's **Logs** view over one day reported **379 logs found**, with the
  newest visible entries at `13:18:50Z`.
- `SHOW EVENT TYPES since 1 day ago` included `Metric`, `Log`, `Span`, and
  `DistributedTraceSummary`, and entity search reported `"reporting": true`.
- After a one-time collector restart and fresh live chat traffic, Render logs
  showed metrics, traces and logs through `2026-11-18T16:24:32Z`, and the New
  Relic entity's **Logs** view showed **30 logs found** with timestamps
  `16:24:52Z`–`16:24:58Z`.

This means the empty **Metrics — We couldn't find any items** panel is not
evidence of a broken ingest key. It is an `EXT|SERVICE` OpenTelemetry entity, so
APM-shaped panels may not populate even though the underlying OTLP event types
are queryable: after fresh ingestion the **Summary** page still showed
`No data from this entity` for web-transaction metrics and distributed tracing.
Use the Logs view and NRQL keyed on `service.name` as the ingestion checks;
re-check after fresh traffic if the collector has been idle.

Do **not** treat a green local observability stack, a green
`test_the_collector_really_builds_the_tracked_config`, or the admin viewer's
`live` badge as proof of dashboard ingestion (see "Viewer status honesty" below).

## Config facts (verified from `docker/otel-collector-config.yaml`)

These are read from the tracked file, not inferred from the UI:

| Fact | Exact config |
| --- | --- |
| Sink endpoint | `exporters.otlphttp/newrelic.endpoint: https://otlp.nr-data.net:4318` — **US region only** |
| Auth header | `exporters.otlphttp/newrelic.headers.api-key: ${env:NEW_RELIC_LICENSE_KEY}` |
| Transport | `compression: gzip`, `sending_queue.enabled: true` backed by `file_storage`, `retry_on_failure.max_elapsed_time: 300s` (a sustained 429/5xx drops the batch after 5 minutes) |
| Entity name | `processors.resource.attributes` **upserts `service.name: nexus-ai-assistant`**, so the entity is named `nexus-ai-assistant`, not `nexus-backend` or the Render service name |
| Other resource attrs | `deployment.environment: ${env:ENVIRONMENT}`, `service.version: ${env:SERVICE_VERSION}` (all `action: upsert`) |
| Sampling | `tail_sampling` keeps 100% of `ERROR` spans, spans slower than 2000ms, and **10% of the rest** — ~90% of healthy traces are intentionally dropped; logs and metrics are **not** sampled |
| Pipelines | `logs`, `metrics`, `traces` all export to `otlphttp/newrelic` (plus `debug`); only `traces` uses `tail_sampling` |
| Receiver | OTLP/HTTP `:4318` requires HTTP basic auth (`basicauth/server`); an unauthenticated request receives **401** |

The endpoint, header name, resource attributes and sampling behaviour match
New Relic's documented OTLP collector pattern
(`docs.newrelic.com/docs/opentelemetry/get-started/collector-processing/opentelemetry-collector-processing-intro.md`).
That is a config fact, not evidence that a key was accepted.

## What the entity GUID actually is

Base64-decoding the reported GUID gives:

```
8594352|EXT|SERVICE|-6144501427358398501
```

- Account ID: `8594352`
- Domain/type: **`EXT|SERVICE`** — New Relic's **OpenTelemetry service** entity,
  **not** an `APM` service entity created by an APM agent.
- Per New Relic docs, an OTEL service is found with
  `name = '<service name>' AND domain = 'EXT' AND type = 'SERVICE' AND accountId = '<account id>'`.

Consequence: looking in **APM & Services** for `nexus-backend` will find nothing
even when OTLP data is arriving. This is also stated in
`docs/deployment-guide.md:285-291`. The correct name to search is
`nexus-ai-assistant` because the collector overwrites `service.name`.

This GUID is plausibly the right entity *kind*. That does **not** prove data
reached it.

## First diagnostic check (run in New Relic UI, account 8594352)

Start with **NRQL**, keyed on the exact `service.name` the collector upserts.
Use metrics and logs as the primary evidence: traces are 90% sampled by design.

```sql
-- 1. Is any OTLP metric from this service queryable?
SELECT count(*) FROM Metric
WHERE service.name = 'nexus-ai-assistant' SINCE 24 hours ago

-- 2. Same for logs.
SELECT count(*) FROM Log
WHERE service.name = 'nexus-ai-assistant' SINCE 24 hours ago

-- 3. Traces last: a low count here can be sampling, not an outage.
SELECT count(*) FROM Span
WHERE service.name = 'nexus-ai-assistant' SINCE 24 hours ago
```

Then confirm the entity identity directly (NerdGraph):

```graphql
{
  actor {
    entity(guid: "ODU5NDM1MnxFWFR8U0VSVklDRXwtNjE0NDUwMTQyNzM1ODM5ODUwMQ") {
      name
      entityType
      guid
    }
  }
}
```

```graphql
{
  actor {
    entitySearch(query: "name = 'nexus-ai-assistant' AND domain = 'EXT' AND type = 'SERVICE' AND accountId = '8594352'") {
      count
      results { entities { name guid entityType } }
    }
  }
}
```

Interpretation:

- **Counts > 0, entity resolves** → ingestion is working; the empty view was an
  entity-selection/UI problem. Use the returned GUID, not an APM view. Attach
  the NRQL output to the NR support ticket.
- **Counts = 0 for all three signals** → continue with the checks below. Do not
  declare a root cause on the basis of the UI alone.

## Discrimination checks (each rules one hypothesis in or out)

### 401 vs 403 — two different auth boundaries

| Where the status comes from | Meaning | Next step |
| --- | --- | --- |
| **401** from `POST https://nexus-otel-collector.onrender.com/v1/traces` | Our **receiver** rejected the app's HTTP basic-auth pair (`OTLP_AUTH_USER` / `OTLP_AUTH_PASSWORD` drifted between the two Render services). This is **not** a New Relic failure. | Run `scripts/check-otel-auth.sh` with the four env vars it documents; copy the collector's pair onto the backend and redeploy. |
| **403** from `POST https://otlp.nr-data.net:4318/v1/traces -H "api-key: <key>"` | **New Relic** rejected the license key (wrong key, wrong key type, or wrong account). | Replace `NEW_RELIC_LICENSE_KEY` on the collector and redeploy. Never commit the value. |

The absence of `otlphttp/newrelic` failures in collector logs argues against a
*current* 403, but does not prove the configured key belongs to account
`8594352` decoded from the GUID.

### US vs EU ingest region

The tracked config is hard-coded to the US endpoint. If the account is EU,
point a probe at both regions with the same key:

```bash
# A minimal but VALID OTLP/HTTP traces payload (an empty body can return 400
# regardless of the key). Same shape scripts/check-otel-auth.sh uses.
curl -s -o /dev/null -w 'US %{http_code}\n' -X POST \
  -H 'api-key: <KEY>' -H 'Content-Type: application/json' \
  --data '{"resourceSpans":[]}' https://otlp.nr-data.net:4318/v1/traces
curl -s -o /dev/null -w 'EU %{http_code}\n' -X POST \
  -H 'api-key: <KEY>' -H 'Content-Type: application/json' \
  --data '{"resourceSpans":[]}' https://otlp.eu01.nr-data.net:4318/v1/traces
```

200 on one region means the key is valid and correctly regional; 403 on both
means the key itself is wrong (`docs/deployment-guide.md:285-288`). If the
account is EU, change the collector's endpoint accordingly — this is a config
fact to verify, not an assumed fault.

### Collector availability and quota

- The collector is a Free Render service and this repository has **no warmup
  cron**. If an external scheduler is used, it is owned outside this repo.
  After idle, the first OTLP batch from the app hits a ~1-minute cold start and
  can be dropped; export resumes on the next batch. This is accepted behaviour
  (`render.yaml:150-157`, `docs/deployment-guide.md:293-301`).
- A free-tier 429 is retried only for `max_elapsed_time: 300s`; a sustained
  quota hit therefore drops batches. Check collector logs for 429s even when
  no other exporter error appears.

## Viewer status honesty

`observability_stack_status()` (`backend/app/services/observability/viewer.py:38-59`)
returns `"status": "live"` when three **config** conditions hold:
`NEW_RELIC_ENABLED` is true, the `opentelemetry` package is importable, and
`NEW_RELIC_OTLP_ENDPOINT` is a non-empty string. It makes **no** network call to
the collector or to New Relic. So "live" means **configured**, not
"telemetry ingested". This is a documented gap: the app code is owned by
another slice, so it is recorded here rather than changed. Do not use the
viewer badge as evidence in an escalation.

## Escalation

1. Attach: account ID `8594352`, the GUID above, the three NRQL results,
   collector export-log excerpts with timestamps, and the 401/403 and US/EU
   probe outputs from the checks above.
2. Counts > 0 but UI empty → New Relic support, entity-display issue; quote the
   `EXT|SERVICE` vs `APM` distinction.
3. Counts = 0 with a verified 200 ingest probe → New Relic support with the
   exact resource attributes (`service.name: nexus-ai-assistant`); do not
   speculate about a cause in the ticket.
4. 403 or wrong region → fix the collector's `NEW_RELIC_LICENSE_KEY` / endpoint
   and redeploy, then re-run the NRQL checks.
5. Any fix gets a postmortem per the runbook README, including a config-only
   revert.
