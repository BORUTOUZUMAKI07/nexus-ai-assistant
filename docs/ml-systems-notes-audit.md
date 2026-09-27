# ML Systems Design Master Notes — Feature Harvest Audit

Maps every part/section of `ml_systems_design_master_notes.md` (Ch. 1–7 +
"Beyond the Book" §8 + Appendix) to the project's actual implementation. Verdicts:

- **[IMPLEMENTED]** — a real feature in this repo covers the notes' point(s).
- **[CANDIDATE]** — genuinely useful for this product and NOT currently built.
- **[OUT-OF-SCOPE]** — by-design not built here (no model training, no self-hosted
  inference); or pure theory/reference with no product mapping.
- **[N/A]** — glossary/reference/decision content.

Audit date: 2026-09-27. Evidence is real file paths; nothing below is invented.
Intentionally mirrors the SDLC of an ML system: data → features → model → eval →
deploy → operate (watch for drift/failure).

Key mapping (evidence legend):
- Storage/ORM: Postgres + SQLModel (`sqlmodel`), JSONB, naive-UTC, `BaseRepository`
- RAG: `backend/app/services/rag/` (base, chunking, retrieval, reranking, query_rewriter,
  citation, critique, ingest)
- Eval: `backend/app/services/evaluation/` (deepeval, ragas, redteam, regression, quality,
  guardrail)
- Monitoring: `backend/app/services/monitoring/drift_{monitor,service}.py`
- Observability: `backend/app/services/observability/` (viewer, metrics, cost_tracking, tracing)
- Experiments: `backend/app/services/experiments.py` + `infrastructure/cache/response_cache.py`
- Ops/deploy: `.github/workflows/ci.yml`, `docker-compose.yml`, Celery `worker/tasks.py`

---

# PART 0 — The One-Page Mental Model / PART 1 — Ch1: ML Systems in Production

| Section | Verdict | Evidence / note |
|---|---|---|
| 1.1–1.2 When to use ML / use cases | **[N/A]** | Reference framing. The product is an AI assistant, not a bespoke-model shop. |
| 1.3 Mind vs data / 1.4 Research vs production | **[N/A]** | Reference. |
| 1.4 Latency vs throughput | **[IMPLEMENTED]** | Streaming (`StreamingResponse` SSE) + per-call model routing (`fast_chat`/`complex_reasoning`) balance latency; async Celery for background work. |
| 1.4 Data in production | **[IMPLEMENTED]** | Real telemetry tables: `usage_logs`, `cost_logs`, `evaluation_logs`. |
| 1.4 Fairness / 1.4 Interpretability | **[CANDIDATE]** | Guardrails/evidence gate exist; no fairness monitoring slice nor explanation surfaces. |
| 1.5–1.6 ML vs traditional software / designing ML systems | **[N/A]** | Reference. |

# PART 2 — Ch2: Data Engineering Fundamentals

| Section | Verdict | Evidence / note |
|---|---|---|
| 2.1 Data sources | **[IMPLEMENTED-part]** | User uploads (files/knowledge) + API/text inputs; no external streaming sources. |
| 2.2 Data formats | **[IMPLEMENTED-part]** | PDF/doc/markdown/code ingest via `services/rag/ingest.py`; JSON API schemas. |
| 2.3 Relational model | **[IMPLEMENTED]** | Postgres + SQLModel (`domain/*/models.py` + `BaseRepository`). |
| 2.3 NoSQL | **[OUT-OF-SCOPE]** | Redis used only as cache (response cache, refresh-token revocation), not a data model. |
| 2.4 Storage engines / OLTP vs OLAP | **[IMPLEMENTED-part]** | OLTP Postgres; OLAP-style telemetry aggregations in drift/cost/viewer queries over the same tables. |
| 2.5 Modes of dataflow | **[IMPLEMENTED-part]** | Request-driven + periodic (Celery beat) flows; no streaming pipeline (no Kafka). |
| 2.6 Batch vs stream processing | **[IMPLEMENTED-part]** | Celery periodic tasks (drift check, webhook redelivery, drift analysis); batch fetch/pagination. |

# PART 3 — Ch3: Training Data

| Section | Verdict | Evidence / note |
|---|---|---|
| 3.1 Sampling (all) | **[OUT-OF-SCOPE]** | No model training here (prompt-based product). |
| 3.2 Labeling / label multiplicity / lineage | **[IMPLEMENTED-part]** | No label pipeline; evaluation verdicts are label-ish and persisted in `evaluation_logs` (provenance of judge/metadata). |
| 3.3 Weak supervision / semi-supervision / transfer learning / active learning | **[OUT-OF-SCOPE]** | No training. |
| 3.4 Class imbalance | **[OUT-OF-SCOPE]** | No classification model. |
| 3.5 Data augmentation | **[OUT-OF-SCOPE]** | No training. |

# PART 4 — Ch4: Feature Engineering

| Section | Verdict | Evidence / note |
|---|---|---|
| 4.1–4.4 Feature ops (all theory) | **[OUT-OF-SCOPE]** | No featurized model; embeddings for RAG are produced upstream by the embedding provider. |
| 4.3 Data leakage — "detection" spirit | **[CANDIDATE-part]** | No train/test split here (nothing trained); the analog of leakage = context leaks in RAG conversations, partially guarded by `citation.py` + retrieval gates. |

# PART 5 — Ch5: Model Development

| Section | Verdict | Evidence / note |
|---|---|---|
| 5.1 Framing problems / objective functions | **[N/A]** | Reference. |
| 5.2 Evaluating & selecting models (six tips) | **[IMPLEMENTED-part]** | Eval scorecards (`evaluation_logs`), quality/ragas services; no learning curves/slicing dashboards. |
| 5.3 Ensembles | **[IMPLEMENTED-part]** | Multi-model routing (`fast_chat`, `complex_reasoning`, subagent models) + judge fallbacks (deterministic marker verdicts with model tie-break) in `regression_service.py`. |
| 5.4 Experiment tracking & versioning | **[IMPLEMENTED]** | `services/experiments.py` (canary/shadow, HMAC buckets, weighted variants) + `prompt_variant` logged; prompt-regression CI gate. |
| 5.5 Debugging ML models | **[N/A]** | No models to debug. |
| 5.6 Distributed training / 5.7 AutoML | **[OUT-OF-SCOPE]** | No training. |
| 5.8 Four phases of ML adoption | **[N/A]** | Reference. |
| 5.9 Offline evaluation | **[IMPLEMENTED]** | Golden-set prompt regression (`regression_service.py`), arena/conversational/RAG gates; offline deterministic fallbacks. |
| 5.9 Calibration / confidence gating in serving | **[CANDIDATE]** | Guardrails are pre-dispatch; no post-hoc confidence gate on generated answers. |

# PART 6 — Ch6: Model Deployment

| Section | Verdict | Evidence / note |
|---|---|---|
| 6.1–6.2 What deploy means / deployment myths | **[N/A]** | Reference. |
| 6.3 Batch vs online prediction | **[IMPLEMENTED-part]** | Online streaming chat; offline/asynchronous paths via Celery (drift scan, eval reviews). |
| 6.3 Micro-batching | **[OUT-OF-SCOPE]** | Managed inference; no serve-time scheduling. |
| 6.4 Model compression | **[OUT-OF-SCOPE]** | No self-hosted models. |
| 6.5 Cloud vs edge | **[OUT-OF-SCOPE]** | Cloud-managed providers (LiteLLM). |
| **6.6 Safe release patterns (canary → rollout, instant rollback)** | **[IMPLEMENTED]** | `experiments.py` (shadow/canary prompt variants) + `response_cache.py` (per-user+model cache) + CI live prompt-regression gates as promotion gate. Closest in-repo analog to "promotion gate (CI/CD for models)". |

# PART 7 — Ch7: Why ML Systems Fail in Production

| Section | Verdict | Evidence / note |
|---|---|---|
| 7.1–7.2 Feedback loops / what is a failure | **[OUT-OF-SCOPE-part]** | No learning from production; the eval/probe loop (redteam, regression) is the failure-detection analog. |
| 7.4 Edge cases | **[IMPLEMENTED]** | Guardrails (`guardrail_service.py`, adversarial scans), safety flag in ARQ node. |
| **7.5 Degenerate feedback loops (popularity-bucketed hit rate, epsilon-greedy, IPS)** | **[CANDIDATE]** | Feedback is logged (`sendMessageFeedback`), but no popularity-bucketed monitoring nor exploration correction. |
| **7.6 Data distribution shifts (sliding-window, PSI, z-score, baseline)** | **[IMPLEMENTED]** | `monitoring/drift_monitor.py` (z-score rate drift + PSI distribution drift, equal-window baseline) + `drift_service.py` over `usage_logs`/`evaluation_logs`; admin endpoint; Celery periodic task; viewer banner. Exactly MD §7.6 sliding-not-cumulative principle. |

# PART 8 — Beyond the Book (Modern Best Practices)

| Section | Verdict | Evidence / note |
|---|---|---|
| 8.1 Reference architecture | **[IMPLEMENTED]** | Layered backend: domain/application/infrastructure + `docs/architecture.md`. |
| 8.2 Testing pyramid for ML | **[IMPLEMENTED]** | Unit (FakeSession, no Docker) → behavioral-invariants → integration (Postgres testcontainers) → e2e (deselected) + live LLM eval gates; CI `ci.yml`. |
| 8.3 Monitoring stack | **[IMPLEMENTED]** | Self-contained viewer (`observability/viewer.py`) — request/token/cost rollups, per-model cost table, eval scorecard, drift snapshot; plus `metrics.py`, `cost_tracking.py`; JSON at `/admin/monitoring/viewer-data` and `/admin/monitoring/observability`. |
| 8.4 CI/CD/CT (continuous training) | **[IMPLEMENTED-part]** | CI full unit suite + informative ruff + live-LLM-eval-gates job. **CT is OUT-OF-SCOPE** (no training). |
| 8.5 Cost & efficiency | **[IMPLEMENTED]** | `cost_tracking.py` (rollups on `cost_logs`), per-model cost in viewer; optional Langfuse/Helicone sinks. |
| 8.6 Security & privacy | **[IMPLEMENTED]** | 2FA (TOTP), PII redaction (`core/redaction.py`, structlog processor), GDPR export/erasure (`account_service`), signed outbound webhooks, HITL approvals, IDOR scoping. |
| 8.7 Responsible ML checklist | **[CANDIDATE-part]** | Guardrails + red-teaming + evidence gate cover most rows; no formal fairness/interpretability monitoring. |
| **8.8 LLM-era additions** | **[IMPLEMENTED]** | Multi-turn+arena evals, RAG faithfulness/relevancy (ragas), drift monitor, prompt regression gate, scaffolding/HITL via elicitations, memory injection, caching. |
| **8.9 Bandits for exploration** | **[CANDIDATE]** | Not implemented anywhere; `experiments.py` uses static weighted buckets, no learning-from-feedback bandit. |

# APPENDIX (A–E)

| Item | Verdict | Evidence / note |
|---|---|---|
| A. Formula sheet / B. Decision tables / D. Glossary / E. Errata | **[N/A]** | Reference. |
| C. Master checklist | **[IMPLEMENTED-part]** | The actionable rows (drift, eval, monitoring, CI, cost, feedback-loop detection) are individually assessed above; the training-specific rows (retrain trigger, OOF-batch checks) don't apply. |

---

# Summary

| Verdict | Count (by section) |
|---|---|
| **[IMPLEMENTED]** | ~26 |
| **[CANDIDATE]** | 5 (fairness/interpretability, calibration/confidence gate, popularity feedback-loop monitoring, bandit exploration, responsible-ML formalization) |
| **[OUT-OF-SCOPE] / [N/A]** | remainder (training, compression, self-hosted inference) |

**Genuine candidates worth building (none is training-dependent):**
1. **Serving confidence gate** (§5.9) — post-generation confidence/quality gate to flag low-confidence answers.
2. **Popularity-bucketed hit-rate & coverage monitoring** (§7.5) — detect degenerate feedback loops using logged feedback.
3. **Bandit-based exploration** (§8.9) — upgrade `experiments.py` from static weighted buckets to a simple epsilon-greedy over logged prompt feedback.
4. **Fairness/interpretability surface** (§1.4, §8.7) — formalize existing guardrail/evidence data into admin-visible slices.

Everything training/inference-serving specific (sampling, augmentation, compression,
distributed training, vLLM-style serving) is **by-design out-of-scope** for a
LiteLLM-managed, provider-agnostic assistant backend.