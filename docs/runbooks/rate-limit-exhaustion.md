# Rate-limit exhaustion

Alert: `NexusRateLimitStorm` — sustained `rate_limit_denied` increments.

## Symptoms
- Page from the alert; `nexus_requests_total{operation="rate_limit_denied"}`
  climbing.
- Users report "try again later" errors; `429` in access logs.
- Optional: `X-RateLimit-Remaining: 0` + `Retry-After` present on responses.

## What the seams do
- `rate_limit(scope, limit, window)` — authenticated endpoints (per-org bucket
  when `org_scope=True`, per-user otherwise).
- `rate_limit_anon(scope, limit, window)` — login/register, keyed by client IP.
- Denied requests get RFC 6585 429 + `Retry-After` + `X-RateLimit-*`.
- **Fail-open:** Redis down ⇒ checks skipped (see
  [redis-fail-open.md](redis-fail-open.md)); a 429 storm *with* healthy Redis is
  real throttling, not an outage.

## Triage (10 minutes)
1. **Which scope?** Locate the hot key: Redis `KEYS rate_limit:*` (or the
   `check_rate_limit` Lua counters) per scope:
   `auth.login`, `tool.execute`, `file.upload`.
2. **Which tenant?** For org-scoped scopes the key is `tool.execute:org:<id>` —
   one busy org can legitimately fill its own bucket without touching others
   (that is the point of per-org sizing). Still page-worthy if it is a big
   tenant.
3. **Legitimate load or abuse?** Correlate with `nexus_requests_total` for that
   operation, IPs from logs, and whether the client respects `Retry-After`.

## Common causes and fixes
| Cause | Fix |
| --- | --- |
| Legit burst (tenant increase, new client) | Raise the scope limit in the call site (`auth.py`/`tools.py`/`files.py`) — limits must stay *below* integration-test volumes |
| Client not honoring 429/Retry-After | Blocklist the bot; correct the client's retry policy |
| Login brute-force protection kicking in (works as designed) | No fix — this is the protector working; log which IPs |
| Badly sized limit vs. a real workload | Re-tune: `rate_limit("tool.execute", limit=20, window_seconds=60)`; keep ≥ 50% headroom under peak |

## Resolution
- Never raise the limit to "make the alert go away" without checking the burst
  is legitimate.
- If a misbehaving tenant: per-org buckets already contain it — verify with the
  key format `scope:org:<org_id>`. For non-org misuse, IP keying +
  blocklist.

## Prevention
- Load-tests (`backend/loadtests/locustfile.py`) should assert the 429 path
  stays below the alert threshold while honoring the limit.
- Keep integration-test volumes below the production limits so the schema and
  the seam don't collide in CI.