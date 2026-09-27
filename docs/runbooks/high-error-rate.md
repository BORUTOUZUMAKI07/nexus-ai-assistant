# High error rate

Alert: `NexusErrorRateHigh` — `rate(nexus_errors_total[5m]) /
rate(nexus_requests_total[5m]) > 0.05` sustained 10m.

## Symptoms
- Page from the alert.
- `nexus_errors_total{error="..."}` grows for a specific `error` label value.
- Users may see failed tool calls or empty assistant replies (guarded by
  retries in `tool_service` / `ai_client`).

## Triage (10 minutes)
1. **Which error dominates?** From Prometheus:
   `topk(10, sum by (error) (rate(nexus_errors_total[10m])))`.
2. **Is it one LLM provider?** Errors labelled `tool_<name>_<exc>` or
   `llm:groq:*` point at an upstream provider incident. Check provider status
   page; retries + `LLMProviderError` fallbacks should absorb most failures.
3. **Is it storage?** `redis_fail_open` counter rising alongside → see
   [redis-fail-open.md](redis-fail-open.md). Fail-open should keep the error
   rate low; a genuine error-rate breach on top of it means business logic
   broke while the dependency was down (e.g. cache-miss storm hitting a slow
   DB → timeouts).

## Common causes and fixes
| Cause | Signature | Fix |
| --- | --- | --- |
| Upstream LLM outage | `error="llm:*"` spike | Rely on existing circuit/retry; consider switching `DEFAULT_MODEL` provider temporarily |
| Tool gateway sandbox timeouts | `error="tool_*_TimeoutError"` | Raise tool `timeout_seconds`, or reduce parallel tool fan-out |
| Rate-limit misconfiguration on a legit path | `error="rate-limit-exceeded"` | See [rate-limit-exhaustion.md](rate-limit-exhaustion.md) — 429s are *not* the error-rate SLI, but an unrelated filter change can shift the denominator; verify with the query above |
| RAG vector-store outage (fail-open path) | errors on `rag:*`, high latency | Vector store is fail-open too; keep its counter visible on the dashboard |
| Regression in a shipped change | sudden, correlates with deploy | `git log` the deploy window; rollback if the delta is a straight regression |

## Resolution
- If regression: roll back the offending change, reopen the incident, and fix
  forward with a test that reproduces the error path.
- If upstream: no code change. Confirm retries/backoff are absorbing the
  provider error; watch the burn rate before letting the SLO drain.
- Track restoration on the same dashboard: error-rate SLI should return below
  1% within the failure window when the cause clears.

## Prevention
- Live LLM-judge gates already protect prompt/evaluator regressions; make sure
  provider-failure drills (kill the mock provider in staging) are run quarterly.