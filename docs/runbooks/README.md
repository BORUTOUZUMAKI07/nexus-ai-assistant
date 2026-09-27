# Runbooks

Operational runbooks for the Nexus AI Assistant backend. Each runbook
corresponds to one or more alerts from
[`docs/alerting/alert-rules.yml`](../alerting/alert-rules.yml).

| Runbook | When to use | Alert(s) |
| --- | --- | --- |
| [Full outage](full-outage.md) | `/metrics` not scraped, app unreachable, 5xx wall | `NexusRequestsDown` |
| [High error rate](high-error-rate.md) | Errors above 5% of traffic sustained 10m | `NexusErrorRateHigh` |
| [Latency SLI burn](latency-sli-burn.md) | p95/p99 latency above budget | `NexusLatencyP95Burn`, `NexusLatencyP99Burn` |
| [Rate-limit exhaustion](rate-limit-exhaustion.md) | Clients throttled with 429s | `NexusRateLimitStorm` |
| [Redis fail-open](redis-fail-open.md) | Redis unreachable, seams failing open | `NexusRedisFailOpenActive` |
| [Indexing backlog](indexing-backlog.md) | Async file-indexing runs stuck PENDING | `NexusIndexingBacklog` (PLANNED) |

## Operating principles

1. **Fail-open everywhere.** Redis, vector store, and rate-limiter outages must
   degrade request quality, **not** availability. If a "fix" knocks the app
   offline, it is the wrong fix.
2. **429s are health, not errors.** Rate-limit counts are an intentional
   protector — a throttle storm is a *tenant* or *limit-sizing* problem, not an
   application crash.
3. **Every page gets a postmortem.** If the fix was a config change or a
   one-line revert, that is still a postmortem.
4. **Verify the fix in staging first** unless the page is at ≥ 14.4x burn, in
   which case ship the known-good revert immediately and stage afterwards.

## Incident severity guide

| Sev | Meaning | Response |
| --- | --- | --- |
| SEV1 | User-visible outage, >10% traffic affected | Page immediately, revert/rollback, postmortem in 24h |
| SEV2 | SLI burning, bounded blast radius | Page, diagnose with the runbook, no risky deploys |
| SEV3 | Drift or non-urgent degradation | Ticket, next-business-day review |