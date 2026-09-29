# Service Level Objectives (SLOs)

> Applies to the Nexus AI Assistant API (backend). Written for operators who
> scrape `/metrics` into Prometheus and page from the rules in
> [`docs/alerting/alert-rules.yml`](alerting/alert-rules.yml).

## 1. Objectives and SLI definitions

All SLIs are measured from process-level telemetry exposed by the
Prometheus `/metrics` endpoint (`backend/app/api/v1/metrics.py`), which renders
the in-process `MetricsCollector` in text-format v0.0.4. **Caveat:** the
collector is in-process and per-instance — with more than one API replica you
must aggregate across instances in Prometheus (each instance carries a
`job="nexus-backend"` label).

| SLI | Definition | Target | Window |
| --- | --- | --- | --- |
| **Availability** | Proportion of requests that complete with a non-5xx response | 99.9% | 30d rolling |
| **Error rate** | `rate(nexus_errors_total[5m]) / rate(nexus_requests_total[5m])` (identified errors, not system 5xx) | ≤ 1% | 30d rolling |
| **Latency p95** | `nexus_latency_seconds{quantile="0.95"}` across chat/tool/RAG operations | ≤ 2.0s | 30d rolling |
| **Latency p99** | `nexus_latency_seconds{quantile="0.99"}` across chat/tool/RAG operations | ≤ 5.0s | 30d rolling |
| **Rate-limit health** | `rate(nexus_requests_total{operation="rate_limit_denied"}[5m])` | No sustained 429 storm: < 5% of total requests throttled | 1h |
| **Dependency fail-open** | `nexus_requests_total{operation="redis_fail_open"}` | Redis outages are fail-open; sustained increments page the operator | event-driven |

**Non-goals (this quarter):** cold-start retrieval quality percentiles,
indexing freshness percentiles for the async 202 pattern, and per-tenant SLO
breakdowns. These need the queue/DB exporters listed in §7.

## 2. Error budget policy

- Each 30d SLO carries an explicit error budget.
- **Burn-rate alerting** (multi-window, from Google SRE workbook §4) is used so
  we page fast on high burn while tolerating brief, cheap spikes:

| Burn rate | Meaning | Page after |
| --- | --- | --- |
| ≥ 14.4x | SLO will be exhausted in < 2h | 10m |
| ≥ 6x | SLO exhausted in ~5h | 1h |
| ≥ 1x (multi-window) | SLO being breached over the quarter | 6h |

- Budget spent triggers an incident review; **> 100%** spent triggers a
  mandatory freeze on risky releases until the back-budget window passes.
- A single well-understood dependency outage that is already mitigated by a
  fail-open path (Redis, vector store) does **not** consume availability
  budget if user-visible success is preserved.

## 3. Ownership and pages

| SLO | Owner |
| --- | --- |
| Availability, latency | Platform on-call (primary) |
| Error rate | Service on-call per owning team |
| Rate-limit health | Platform on-call |
| Redis fail-open | Platform on-call + SRE |

Night/weekend pages only fire for burn rates ≥ 6x (or ≥ 14.4x); 1x alerts are
tickets, not pages.

## 4. Monitoring configuration

1. Check `METRICS_TOKEN` (and every other secret) is set — never ship the
   scrape endpoint open to the public internet. In `docker-compose`/k8s add a
   Prometheus scrape job:

   ```yaml
   scrape_configs:
     - job_name: nexus-backend
       metrics_path: /metrics
       bearer_token: ${METRICS_TOKEN}   # or via a mounted secret file
       static_configs:
         - targets: ["backend:8000"]
   ```

2. Record rules + alert rules from `docs/alerting/alert-rules.yml`
   (validate locally with `promtool check rules` — not wired into CI yet).

3. Verify the endpoint manually (dev, token unset):

   ```bash
   curl -s localhost:8000/metrics | head
   # # HELP nexus_requests_total ...
   ```

## 5. Known measurement caveats

- **Fail-open seams** (Redis cache, rate limiter): outages produce
  `redis_fail_open` counters and degraded latency, **not** 5xx errors. SLO
  dashboards should graph them alongside — a p95 latency alert during a Redis
  outage is a symptom, not a regression.
- **In-process percentile math** in `MetricsCollector` uses a 1,000-sample
  sliding buffer per operation and `sorted_samples[int(n * q)]` indexing. This
  is a lightweight approximation, not `histogram_quantile`; percentiles are
  stable enough for burn-rate pages but not for contractual reporting.
- **Rate-limit 429s are not errors** in the error-rate SLI; they are
  intentional protectors and tracked by their own counter.

## 6. SLO ↔ alert-rule mapping

| Alert | SLO it protects | Severity |
| --- | --- | --- |
| `NexusErrorRateHigh` | Error-rate SLO | page |
| `NexusLatencyP95Burn` / `NexusLatencyP99Burn` | Latency SLOs | page |
| `NexusRateLimitStorm` | Rate-limit health | page |
| `NexusRedisFailOpenActive` | dependency fail-open visibility | page |
| `NexusRequestsDown` | Availability / scrape health | page |

## 7. Roadmap exporters

- **Postgres exporter** for indexing-backlog freshness
  (`files.index_status` → Prometheus gauge) — required by the
  `NexusIndexingBacklog` rule (currently commented as `PLANNED`).
- Per-tenant SLO breakdowns using the `org_id` column added on `usage_logs` /
  `cost_logs` (multi-tenant isolation work).