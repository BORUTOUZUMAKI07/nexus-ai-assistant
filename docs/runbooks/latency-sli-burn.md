# Latency SLI burn

Alert: `NexusLatencyP95Burn` (p95 > 2.0s) or `NexusLatencyP99Burn` (p99 > 5.0s).

## Symptoms
- Page from the alert; `nexus_latency_seconds{quantile="0.95"}` above budget.
- Chat responses feel slow; SSE streaming starts late.

## Triage (10 minutes)
1. **Which operation is slow?** `topk(10, nexus_latency_avg_seconds)` — typical
   hot labels: `tool:web_search`, `experiment:chat_system_prompt:*`,
   `span:*` (tracing names). A slow tool backend (web search, code runner)
   inflates chat end-to-end latency, not the model call itself.
2. **Is it the LLM call?** Model latency dominates `llm:*` ops. Check provider
   status + `latency_ms` in `usage_logs` (query via `/api/v1/usage/evaluations`
   or the admin viewer).
3. **Is Redis down?** Fail-open response caching bypass → every request does a
   full model round-trip instead of hitting cache. Cross-check
   `redis_fail_open` counter → see [redis-fail-open.md](redis-fail-open.md).
4. **Concurrency exhaustion?** Pool saturation (DB `AsyncSession` pools, Redis
   connection pool, or the streaming slot limiter
   `_acquire_stream_slot` in `conversations.py`) queues requests. Watch
   `nexus_requests_total` flatline while latency climbs — that is queueing.

## Common causes and fixes
| Cause | Signature | Fix |
| --- | --- | --- |
| Redis down (cache bypass) | `redis_fail_open` + latency rise | Restore Redis; failures are fail-open by design |
| Provider slow | `llm:*` p95 high, provider status degraded | Shift traffic: provider/model switch via `DEFAULT_MODEL` config |
| Multiplexed tools | one slow tool holds the turn | Raise per-tool timeout; enforce deadline via `gather(..., timeout=...)` already present in orchestration |
| Streaming slot exhaustion | many concurrent SSE threads | Tune `MAX_STREAM_CONCURRENCY`; scale replicas |
| Postgres bloat on conversations/messages | p95 on `conv:*` spans | `VACUUM ANALYZE`; add missing index (see drift/`on_index_check` reports in admin) |

## Resolution
- Alleviate first (scale, switch provider, restore dependency), then fix root
  cause forward.
- If a deploy correlates, open the `git log` for the window; latency regressions
  are usually large DB queries or newly-added per-request work (e.g. the org
  resolution lookup added with multi-tenant attribution — it is one indexed
  `organization_members` SELECT; watch for accidental N+1 variants).

## Prevention
- Trend `nexus_latency_avg_seconds` per operation on the SLO dashboard.
- Load-test `backend/loadtests/locustfile.py` before releasing paths that touch
  the DB or external providers (chunk index — see docs/slo.md).