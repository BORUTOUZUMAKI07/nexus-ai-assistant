# Per-stage RED metrics and event-name contract

> **Status: mixed.** The `nexus_stage_*` families and the BFF/outbox hooks below
> are still a contract only — this document fixes their names and label budget
> before code lands so the series stay bounded; all `outbox.*` names remain
> proposed, not observed. Separately, the backend's existing families
> (`nexus_requests_total`, `nexus_errors_total`, `nexus_latency_seconds`) now
> carry this vocabulary for HTTP requests (§1a) and outbox deliveries (§4)
> without introducing new family names.

## Why a separate contract

The existing SLO families (`nexus_requests_total`, `nexus_errors_total`,
`nexus_latency_seconds` — see [slo.md](slo.md)) are emitted in-process by the
backend and arrive at New Relic over OTLP. They answer "is the API healthy?".
They cannot answer "which hop of a user journey was slow?" without a label set
that would explode in cardinality if it were copied naively from raw paths.

This contract adds a **stage** dimension with two values and a strict label
allow-list. It is deliberately small enough that the whole RED surface fits in
a few thousand series per environment.

## 1. Label budget (the important part)

### Allowed labels

| Label | Values | Bound |
| --- | --- | --- |
| `stage` | `bff`, `outbox` | 2 (fixed enum) |
| `route` | Normalized route **template**, e.g. `/api/conversations/[id]/messages/[msgId]` | ~75 templates from `frontend/src/app/api/**/route.ts` |
| `method` | `GET`, `POST`, `PATCH`, `PUT`, `DELETE` | 5 |
| `status_class` | `2xx`, `4xx`, `5xx` (or `1xx`/`3xx` when relevant) | ≤ 5 |
| `outcome` | `success`, `validation_error`, `auth_error`, `upstream_error`, `timeout`, `dropped`, `retried`, `dead_letter` | 8 (fixed enum) |
| `task_type` | Outbox enum from §4 | ≤ 8 (fixed enum) |

Worst case: `2 * 75 * 5 * 5 = 3,750` series for the request families, plus the
outbox families below. With three duration quantiles that is still well under a
few tens of thousands of series — predictable and cheap on a free-tier sink.

### Forbidden labels — never add these

- Raw URL paths, query strings, or path parameter **values** (`[id]`, `[token]`,
  `[userId]`, file names). Use the template with the placeholder intact.
- User IDs, org IDs, conversation IDs, message IDs, session or JWT values.
- Model names, prompt text, error messages, stack traces, tenant names. Use the
  `outcome` enum instead.
- Free-form `status_code` integers. Use `status_class`; the exact code belongs
  in a log event, not a metric label.
- Anything derived from an unbounded set. If a value can grow with traffic, it
  is not a label — put it in a span attribute or a structured log.

**Rule of thumb:** before adding a label, write down its maximum possible value
count. If the count depends on the number of users, requests or documents,
reject it.

### 1a. Backend HTTP labels (implemented)

The backend's existing request metrics use this vocabulary without introducing
new family names. The middleware resolves `route` from the matched FastAPI
route template in `scope["route"]` *after* routing, so
`/api/v1/conversations/{conversation_id}` is one series even when thousands of
conversation IDs are requested. Requests that never matched a route (404s and
pre-routing failures) collapse to the single value `unmatched`. `method` and
`status_class` are bounded as above; raw paths, UUIDs and long numeric IDs are
rejected before they can become labels. The raw path stays available as a trace
attribute (`url.path`) for debugging.

## 2. RED metric families (proposed)

Rate, Errors, Duration, keyed on the labels above:

| Family | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `nexus_stage_requests_total` | counter | `stage`, `route`, `method`, `status_class` | **Rate** — requests/operations completed |
| `nexus_stage_errors_total` | counter | `stage`, `route`, `method`, `outcome` | **Errors** — classified failures (subset of the above) |
| `nexus_stage_duration_seconds` | histogram | `stage`, `route`, `method` | **Duration** — latency distribution |

Plus two outbox-only gauges:

| Family | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `nexus_outbox_queue_depth` | gauge | `task_type` | Rows awaiting delivery; a sustained rise is the backlog signal |
| `nexus_outbox_oldest_pending_seconds` | gauge | `task_type` | Age of the oldest undelivered row; catches a stuck worker faster than depth alone |

Bucket boundaries for the histogram: `0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30`
seconds. The long tail matters: the BFF has a ~55s upstream timeout and a
Render cold start can add ~60s, so a histogram that stops at 5s hides exactly
the failure mode this slice exists to observe.

Derived SLI expressions (same shape as [slo.md](slo.md) §1):

```
rate(nexus_stage_requests_total{stage="bff",status_class!="5xx"}[5m])
  / rate(nexus_stage_requests_total{stage="bff"}[5m])          # availability
rate(nexus_stage_errors_total{stage="bff"}[5m])
  / rate(nexus_stage_requests_total{stage="bff"}[5m])           # error rate
histogram_quantile(0.95, nexus_stage_duration_seconds{stage="bff"})  # p95
```

## 3. BFF event-name helper list

Names are dot-separated: `bff.<domain>.<action>`. The `route` label stays the
normalized template; the event name is the stable machine-readable identity.
These are derived from the route files under `frontend/src/app/api/**` (read
only when writing this contract — no frontend file is modified here).

| Domain | Event names | Route templates |
| --- | --- | --- |
| auth | `bff.auth.login`, `bff.auth.logout`, `bff.auth.refresh`, `bff.auth.register`, `bff.auth.me`, `bff.auth.oauth_start`, `bff.auth.oauth_callback` | `/api/auth/login`, `/api/auth/logout`, `/api/auth/refresh`, `/api/auth/register`, `/api/auth/me`, `/api/auth/oauth/[provider]`, `/api/auth/oauth/[provider]/callback` |
| conversation | `bff.conversation.create`, `bff.conversation.get`, `bff.conversation.list`, `bff.conversation.fork`, `bff.conversation.share`, `bff.conversation.plan` | `/api/conversations`, `/api/conversations/[id]`, `/api/conversations/[id]/fork`, `/api/conversations/[id]/share`, `/api/conversations/[id]/plan` |
| message | `bff.message.list`, `bff.message.create`, `bff.message.feedback`, `bff.message.regenerate` | `/api/conversations/[id]/messages/[msgId]` (plus `/feedback`, `/regenerate`), `/api/hitl` |
| files / RAG | `bff.file.upload`, `bff.file.get`, `bff.file.download`, `bff.file.rag_query` | `/api/files`, `/api/files/[id]`, `/api/files/[id]/download`, `/api/files/rag/query` |
| media | `bff.audio.transcribe`, `bff.audio.speech`, `bff.image.generate` | `/api/audio/transcribe`, `/api/audio/speech`, `/api/images/generate` |
| org / project | `bff.org.get`, `bff.org.members`, `bff.org.invites`, `bff.project.list`, `bff.project.get` | `/api/orgs/[id]`, `/api/orgs/[id]/members`, `/api/orgs/[id]/invites`, `/api/projects`, `/api/projects/[projectId]` |
| settings | `bff.settings.get`, `bff.settings.keys`, `bff.settings.memories` | `/api/settings`, `/api/settings/keys`, `/api/settings/memories`, `/api/settings/memories/[id]` |
| artifacts / tools / usage | `bff.artifact.list`, `bff.artifact.get`, `bff.tools.list`, `bff.usage.summary` | `/api/artifacts`, `/api/artifacts/[id]`, `/api/tools`, `/api/usage/summary` |
| plans / webhooks | `bff.plan.approve`, `bff.plan.reject`, `bff.webhook.list`, `bff.webhook.deliveries` | `/api/plans/[id]/approve`, `/api/plans/[id]/reject`, `/api/webhooks`, `/api/webhooks/[id]/deliveries` |
| admin / health | `bff.admin.audit`, `bff.admin.users`, `bff.admin.monitoring`, `bff.health.check` | `/api/admin/*`, `/api/health` |

`bff.health.check` is a user-facing BFF route. Any traffic generated by an
external warmup scheduler is owned outside this repository and should be
excluded from SLI numerators so a heartbeat storm cannot mask a real traffic
drop.

## 4. Background outbox events (partly implemented)

The durable outbox is deployed and its state transitions are logged. Only the
`outbox.deliver` RED sample is instrumented so far; the other names below remain
proposed. `task_type` comes from the handler registry, never a payload string,
and is collapsed to `unmatched` if it is not a safe token. An unrecognised
outcome is collapsed to `dropped`. The implemented sample uses the existing
`nexus_requests_total` / `nexus_errors_total` / `nexus_latency_seconds` families
with `stage=outbox`, `task_type` and `outcome` OTel attributes; the
`nexus_stage_*` families in §2 are still proposed.

| Event | Status | `outcome` values |
| --- | --- | --- |
| `outbox.enqueue` | log only | `success`, `validation_error` |
| `outbox.claim` | log only | `success`, `dropped` |
| `outbox.deliver` | metric + log + span | `success`, `retried`, `dead_letter`, `dropped` |
| `outbox.retry` | log only | `retried`, `dropped` |
| `outbox.dead_letter` | log only | `dead_letter` |
| `outbox.mark_done` | logged as `outbox.deliver` success | `success` |

Actual `task_type` values today: `semantic_cache_invalidate` and
`webhook_dispatch`. The earlier reserved names (`message.persist`,
`index.upsert`, `webhook.deliver`, `usage.record`, `notification.send`) remain
reserved, not emitted.

## 5. Ownership and rollout

- **This document** owns the names and the label allow-list. Changing a name or
  adding a label requires updating this file first.
- **Application instrumentation**: the backend HTTP middleware and outbox
  delivery worker are instrumented. BFF route handlers remain out of scope for
  this document.
- **Collector config** already forwards all three OTLP signals; no new
  exporter or pipeline is needed for these families. They use the existing
  `otlphttp/newrelic` path documented in
  [observability-deployment.md](observability-deployment.md).
- Validate cardinality after rollout with an NRQL distinct count per label; a
  `route` value containing a UUID or numeric ID means a raw path leaked into
  the label and must be fixed before the next deploy.
