# Industry-Gap Implementation Summary (MD §6–9 benchmark)

Everything below maps to the ⚠️/❌ rows from the industry benchmark delivered in
this session. Each item is **code-complete, env-gated (no default behavior
change), unit-tested, and live-verified** unless marked infra-only.

## 1. MCP advanced primitives — Elicitations ✅ live-verified
- `backend/app/services/tools/elicitations.py` — structured human-input requests
  parked server-side, reusing the verified single-use atomic HITL claim machinery
  (SEC-06): owner-scoped, consumed exactly once, expiry-bounded.
- Endpoints: `POST /tools/elicitations`, `POST /tools/elicitations/{id}/respond`.
  Schema-validated answers; single-use repeat → `409`; cross-user → `404`;
  missing required properties → `422`. Live-verified 200/409/422/404.
- `POST /tools/approval` now rejects `__elicitation__` rows with a clear `409`
  (they are not tool runs). Live-verified.
- `backend/app/mcp/server.py` — `nexus://system/capabilities` resource declares
  Elicitation/Roots/Sampling support honestly (Sampling is a client-owned
  primitive and is marked `supported: false`), plus `request_user_input` bridge
  tool and declared `roots`. FastMCP renders each declaration.
- SSE: `elicitation_request` is now a forwarded custom event type.
- Tests: `tests/test_elicitation.py` (5).

## 2. AG-UI standardized streaming (TOOL_CALL_START / TOOL_CALL_COMPLETE) ✅
- `backend/app/api/v1/conversations.py` — tool lifecycle now emits both the
  legacy `tool_call`/`tool_result` events and AG-UI-shaped aliases with
  `messageId`/`tool`/`input`/`output`/`timestamp` and live timestamps. Existing
  clients are untouched (extra event types are ignored by old UIs).

## 3. Evaluation — arena + multi-turn + prompt-regression CI gate ✅
- `deepeval_service.py` — `evaluate_arena_pair` (ArenaGEval w/ deterministic
  groundedness+informativeness fallback) and `evaluate_conversational`
  (ConversationalGEval w/ offline compliance scan).
- `backend/app/services/evaluation/regression_service.py` — guardrail golden set
  (financial-advice refusal, calculator honesty, medical safety, no-evidence
  fabrication) with LLM-as-judge and an offline gate that always runs.
- Admin endpoints: `/admin/evaluation/arena`, `/admin/evaluation/conversational`,
  `/admin/evaluation/prompt-regression` (all persisted to `evaluation_logs`).
- CI gate: `tests/test_prompt_regression_gate.py` runs the compiled system prompt
  against the golden set when `RUN_LLM_EVAL_GATES=1` (opt-in, skipped otherwise).
- Celery: `tasks.prompt_regression_review_task`.
- Tests: `tests/test_eval_upgrades.py` (7).

## 4. Observability — Langfuse wiring (optional) ✅ careful no-op default
- `litellm_client.py::_configure_langfuse()` — registers LiteLLM's built-in
  `langfuse` success/failure callbacks when `LANGFUSE_ENABLED` + keys are set and
  the package is installed (`pip install -e .[observability]`). Default no-op.
- `.env.example` documents the keys.

## 5. LLM-era drift monitoring (the ❌ row) ✅
- `backend/app/services/monitoring/drift_monitor.py` — pure sliding-window math:
  z-score rate drift, PSI distribution drift, equal-window baseline comparison
  (MD §7.6 principles), zero noise for insufficient data.
- `drift_service.py` — reads real telemetry: latency/cost/error-rate from
  `usage_logs`, faithfulness-pass + hallucination rates from `evaluation_logs`.
- Endpoint `GET /admin/monitoring/drift` (admin-only, live 403-checked).
- Celery: `tasks.periodic_drift_check_task`.
- Tests: `tests/test_drift_monitor.py` (8).

## 6. PII redaction (MD §8.6 log hygiene) ✅
- `backend/app/core/redaction.py` — emails, phones, SSNs, credit cards, IPs,
  AWS keys, bearer/API tokens, private keys, home paths; recursive scrubber for
  structured events and name-based secret-value replacement.
- Wired as a structlog processor in `core/logging.py` (only when
  `PII_REDACTION_ENABLED=true`). Guards request-log hygiene and tracebacks.
- Tests: `tests/test_redaction.py` (7).

## 7. Canary/shadow prompt release + response caching ✅
- `backend/app/services/experiments.py` — HMAC-stable user buckets, weighted
  variant assignment, YAML-configurable experiments; zéro behavior change when
  unconfigured (`default` variant). Wired into the sync chat path with
  `prompt_variant` logged to usage metadata + metrics.
- `backend/app/infrastructure/cache/response_cache.py` — per-user+model,
  normalized-exact-query response cache with TTL; fail-open; wired into
  `conversations/{id}/messages` (cached hit metadata recorded). Active only when
  `RESPONSE_CACHE_ENABLED=true`.
- Example experiment config: `backend/config/experiments.example.yaml`.
- Tests: `tests/test_experiments.py` (7), `tests/test_response_cache.py` (7).

## Not implemented (infra/managed-platform only — honest disclosure)
- **K8s/OPA/mesh-level policy (Admission/OPA Gatekeeper, service mesh mTLS)** —
  deployment-time platform work; the in-app 5-step Tool Gateway + RLS remains the
  enforcement point in code. Needs a real K8s cluster, not repo code.
- **Managed APM agents (Datadog/New Relic), managed drift services** — replaced
  by the self-contained Langfuse + drift modules above so the repo stays
  self-service on the free tier.

## Verification
- `pytest` (unit tier): **131 passed** (baseline 90 → 41 new tests, zero
  regressions). Live 27-check REST sweep re-run; every security invariant
  (SEC-01 client-declared approval powerlessness, single-use 409, cross-user
  404, admin 403) re-verified against the new build.