# HLD Pattern Coverage — Nexus AI

Status audit of the 20-pattern HLD checklist (and the 8 prioritized patterns)
against the actual codebase. Each row cites the concrete implementation seam.

Legend: ✅ implemented · ⚠️ partial · ❌ missing/deferred · ➖ not applicable

## The 20 patterns

| # | Pattern | Status | Where it lives in Nexus AI |
|---|---------|--------|----------------------------|
| 1 | API Gateway / Backend-for-Frontend | ✅ | Next.js App Router proxy pass-through (`frontend/src/app/api/`), FastAPI `api/v1/*` routers, JWT auth (`api/deps.py::get_current_user`), Upstash Redis rate limit |
| 2 | Modular Monolith / Layered Architecture | ✅ | domain → services → api/agents/infrastructure layering throughout `backend/app/`; routers never touch the DB |
| 3 | Event-Driven Architecture | ✅ | Celery (`worker/celery_app.py`, `worker/tasks.py`): file indexing, turn evaluation, drift check, webhook retry |
| 4 | Queue-Based Load Leveling | ✅ | Celery + Redis broker; `ASYNC_INDEXING` defers heavy embedding work to workers; `rate_limit` + `worker_prefetch_multiplier=1` on indexing tasks |
| 5 | Saga Pattern | ⚠️ | Targeted saga: `FileService.delete_file` (storage → DB → vectors, abort-on-storage-failure), GDPR erasure + conversation delete now purge Qdrant + object storage fail-open; no generic compensation engine (deliberately scoped) |
| 6 | Transactional Outbox | ❌ | No event bus/outbox table. Not built: webhook delivery is already retry-durable via `retry_webhook_deliveries`, and no other async consumers need guaranteed event publish today. Revisit if a second consumer of ingestion/approval events appears |
| 7 | Idempotency Pattern | ✅ | Infra-level: idempotent Qdrant collection/point ops, wipe-before-reinsert, bucket create, webhook retry. API-level: `Idempotency-Key` header enforced on `POST /tools/execute`, `/tools/approval`, `/files/upload` (409 on replay, Redis-backed, fail-open) |
| 8 | Circuit Breaker + Retry with Backoff | ✅ | `infrastructure/resilience/guards.py::CircuitBreaker` (per model-group, closed→open→half-open probe) wired into `litellm_client`; litellm fallback chains + per-model timeouts; Celery `max_retries`/`backoff`; frontend `fetchWithRetry` exponential backoff |
| 9 | Bulkhead | ✅ | `guards.py::InFlightLimiter` — per-provider-group in-flight semaphore held for the whole stream; Celery worker separation + `worker_max_tasks_per_child` |
| 10 | Cache-Aside | ✅ | `infrastructure/cache/response_cache.py` get→miss→set, TTL-bounded, per-user+model+variant keyed, strictly fail-open |
| 11 | CQRS (where justified) | ⚠️ | Read-heavy admin surfaces (slices/fairness/bandit/audit reports) are on-demand read models over the same store; no separate read replicas (single-node scale) |
| 12 | DB Sharding / Read Replicas | ⏸ | Explicitly future ("Future PostgreSQL scaling" in the checklist) — deferred by design |
| 13 | Consistent Hashing | ⏸ | Conditional on scale ("if scale requires") — deferred by design |
| 14 | Fan-Out/Fan-In | ✅ | Celery `group` for parallel indexing (`tasks.py::trigger_indexing_pipeline`), LangGraph supervisor + subagents, parallel RAG retrieval |
| 15 | Orchestrator–Worker | ✅ | Compiled LangGraph `StateGraph` + `AsyncPostgresSaver` + Celery workers |
| 16 | Human-in-the-Loop / Approval | ✅ | LangGraph HITL approval gates; server-side approve→execute (`POST /tools/approval`), plan-then-approve, elicitation single-use |
| 17 | RAG Pipeline | ✅ | Hybrid dense+BM25, parent-child reindex, RRF fusion, FlashRank rerank, budget-capped generation, citation attribution, CRAG corrective retrieval |
| 18 | Strangler | ➖ | No legacy modules being replaced |
| 19 | Sidecar / Ambassador | ⚠️ | External telemetry (Sentry/Helicone/Langfuse via LiteLLM callbacks); no sidecar containers |
| 20 | Leader Election / Distributed Locking | ✅ | `guards.py::singleton_lock` (Redis `SETNX` + TTL) guards `cache_cleanup`, `periodic_drift_check`, `prompt_regression_review`, `retry_webhook_deliveries` against double-run on multi-worker beat |

## The 8 prioritized patterns

| Priority pattern | Verdict | Notes |
|---|---|---|
| 1. Transactional Outbox + Idempotent Consumer | ❌ **not built** | Idempotent-consumer aspects exist (ingestion reindex, webhook retry); the outbox half is deferred until a second async consumer needs guaranteed publish |
| 2. Saga + Reconciliation | ⚠️ targeted | Cross-store deletes done (GDPR + conversation + file); fail-open vector/object cleanup logs stragglers for a future reconciliation sweep |
| 3. Queue Load Leveling + Backpressure | ✅ | Celery/Redis leveling + task `rate_limit`; backpressure is producer-side bounded retries |
| 4. Circuit Breaker + Bulkhead | ✅ **implemented now** | `guards.py` + `litellm_client` wiring; per-group in-flight cap |
| 5. Orchestrator–Worker + Fan-Out/Fan-In | ✅ | LangGraph + Celery canvas |
| 6. API Gateway + Zero-Trust Authorization | ⚠️ | Centralized authn/authz (JWT, MFA, org-scoped rows, signed client approvals, Idempotency-Key on side-effecting routes); explicit workload identity / per-request attestation not implemented (single-service deployment) |
| 7. Cache-Aside + Invalidation | ✅ | Cache-aside with TTL + cleanup task; keyed per `prompt_variant` so bandit/canary flips never serve stale-variant answers. Event-driven invalidation not needed (chat answers are immutable once cached) |
| 8. Canary Release + Rollback | ⚠️ app-level | ε-greedy bandit variant selection + margin-gated prompt-optimizer promotion = app-level canary/rollback; deployment-level canary (blue-green infra) not implemented (single deployment) |

## What was implemented in this pass (Sept 2026)

New module `backend/app/infrastructure/resilience/guards.py` (+ `__init__.py`):

- **CircuitBreaker** — per-name closed→open→half-open state machine blocklisting a
  dead provider group so fallback chains don't grind through every group.
- **InFlightLimiter** — per-group asyncio semaphore (bulkhead) capping concurrent
  provider calls; a long stream holds its slot for the full stream.
- **TaskLockStore / singleton_lock** — Redis `SETNX`+TTL lock; periodic Celery
  tasks run only when their lock is held (leader-election lite). Store outage →
  skip this cycle (never double-run).
- **IdempotencyGuard** — client-supplied `Idempotency-Key` replay detection
  (fail-open; duplicate → caller answers 409).

Wired into:

- `infrastructure/ai/litellm_client.py::LiteLLMService` — `complete()` and
  `astream()` check the breaker and hold the in-flight slot per call.
- `services/account_service.py::delete_account` — GDPR erasure now purges the
  user's Qdrant vectors (`delete_by_filter({"user_id": ...})`) and object-storage
  blobs, best-effort fail-open (erasure succeeds even when infra is down).
- `services/conversation_service.py::delete_conversation` — purges the
  conversation's file vectors so deleting a conversation cannot leak its content
  into later RAG retrievals.
- `worker/tasks.py` — `_run_locked` guard + `rate_limit`/`acks_late` on indexing;
  `celery_app.py` adds `worker_max_tasks_per_child`.
- `api/deps.py::require_idempotency_key` + `api/v1/tools.py` (`/execute`,
  `/approval`) + `api/v1/files.py` (`/upload`).
- `infrastructure/cache/response_cache.py` — cache key now includes
  `prompt_variant`; `api/v1/messages.py` passes the bandit-chosen variant so a
  variant flip can never reuse a previous variant's answer.

Tests: `backend/tests/test_resilience_guards.py` (16), `test_account_gdpr.py` +2,
`test_response_cache.py` +2. Full backend suite: **297 passed / 4 skipped**
(71 testcontainers errors are the known no-Docker environmental baseline).

## Operation notes

- Breakout in-flight caps are process-local: a multi-instance deploy gets N× per
  instance (acceptable; documented tradeoff). Reset breakers by redeploying.
- `singleton_lock` double-run protection assumes all beat workers share the same
  Redis (they do — single `REDIS_URL`).
- `Idempotency-Key` marks happen **before** endpoint processing; a request that
  executes then errors still consumes its key within the 24h TTL — clients must
  mint a fresh key for a genuinely new request.