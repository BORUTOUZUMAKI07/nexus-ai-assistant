# Redis fail-open

Alert: `NexusRedisFailOpenActive` — `increase(nexus_requests_total{operation="redis_fail_open"}[5m]) > 0`.

## What fails open and what does not
| Seam | Behavior when Redis is down |
| --- | --- |
| Rate limiter (`rate_limit*`) | All checks skipped, traffic **allowed**, headers absent; `redis_fail_open` counter incremented |
| Response cache (`response_cache`) | Cache misses, full path executed, no pages served stale |
| State store (OAuth PKCE, idempotency) | `IdempotencyGuard`/OAuth state lookups fail — endpoints relying on state may 500 |

So: the app stays **up** and serving, but every request does the full expensive
path (model call) and authentication flows that need Redis-held state degrade.

## Symptoms
- Page from the alert.
- Latency jump on chat/tool ops (cache bypass) → may also fire
  `NexusLatencyP95Burn`; treat as the same incident.
- Rate-limit headers vanish from responses.

## Triage
1. Confirm Redis is actually down (not a DNS/network blip on the app side):
   `redis-cli ping` from the app host; check the Redis provider status page.
2. Check the app logs for `rate_limit_check_failed_fail_open` — it names the
   failure.
3. If only *some* requests fail open, check the connection pool settings — a
   partially exhausted pool can look like an outage.

## Resolution
1. Restore Redis health first (restart, provider mitigation, or failover).
2. **Migrations/keyspace note:** the rate-limit bucket counters and OAuth state
   keys are ephemeral — restoring Redis resets them (limits restart fresh,
   which is safe). Any durable Redis data (if used for caching model outputs)
   is a cache, not a source of truth: no recovery action needed.
3. After restore, confirm `redis_fail_open` increments stop and `X-RateLimit-*`
   headers return.

## When fail-open is NOT safe
- **Idempotency**: duplicate POSTs during the outage were not deduped. Tell
   clients to retry idempotently; the `Idempotency-Key` contract still applies.
- **OAuth state**: an in-flight browser SSO flow started before the outage may
   fail its callback; users retry. Verified-email find-or-create still works —
   only the state exchange degrades.

## Prevention
- Run quarterly chaos drills that kill Redis in staging and confirm the app
  exits the fail-open path cleanly.
- Keep `redis_fail_open` on the SLO dashboard so operators can always
  distinguish "dependency degraded" from "we broke it".