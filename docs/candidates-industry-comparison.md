# Candidate Features vs Industry SOTA — Research Comparison

Research date: 2026-09-27. Method: live web research against current papers
(2024→Sept 2026), vendor docs, and production practice, for the 8 "candidate"
features flagged in the two harvest audits. Verdicts below are the evidence-based
outcome of comparing each candidate against what the industry now actually does.

Legend: ✅ keep & build — the candidate matches or exceeds current practice.
🔁 revise — the candidate was mis-scoped; a different technique is the real fix.
⛔ reclassify — by-design out-of-scope on this stack (managed providers).

---

# Group A — AI-Engineering notes candidates

## A1. Verbalized Sampling (§2.3) — 🔁 REVISED: real fix is structured output, not VS

**What the notes/paper actually say.** VS (Zhang et al., arXiv:2510.01171, Sep 2025;
repo `CHATS-lab/verbalized-sampling`) is a *training-free diversity* technique: it
prompts the model for a *distribution* ("generate 5 responses with their
probabilities") to recover the diversity that RLHF alignment (typicality bias)
removed. Gains: 1.6–2.1× diversity in creative writing / dialogue / open-ended QA /
synthetic data. It is **not** a correctness/reliability tool.

**Where my earlier framing was wrong.** The audit-pitch ("generate N, verbalize
reasoning, pick the best") described **self-consistency** — a different, older
technique (Wang et al. 2022) — not Verbalized Sampling. VS won't make plans more
reliable; it makes output more diverse, which is the opposite of what a plan
schema wants.

**What industry does for the *plan-reliability* goal instead:**
- **Schema-enforced structured output** — 2026 production standard. Prompt-only JSON
  fails 5–15% even on strong models; constrained decoding / API strict-mode
  (`response_format`, `response_mime_type`) guarantees schema compliance. The repo
  currently uses prompt-only JSON + heuristic fallback in `plan_service.py` — this
  is the decoupled-but-cheap tier, and the real opportunity.
- **Retry-with-repair** on parse/strict-validation failure (3-tier reliability
  architecture described in 2026 production guides).
- **Confidence-Informed Self-Consistency (CISC)** — Google, ACL 2025 Findings: since
  true self-consistency is expensive (N paths), weight the vote by model confidence;
  cuts required paths ~40% on average. This is the *sampling* upgrade that actually
  fits the `complex_reasoning` path if we want it.

**Verdict.** 🔁 Do not build "VS for plan robustness". Keep VS on the shelf (it is
SOTA for diversity, but that's only relevant to creative/synthetic generation, not
an assistant core). The genuinely useful, industry-aligned items are: (1) strict-mode /
schema-enforced structured output (§2.4 family) and (2) optional CISC-style weighted
self-consistency for the complex-reasoning path.

---

## A2. REFRAG (§4.11) — ⛔ RECLASSIFIED: out-of-scope; real fix is corrective RAG

**What it actually is.** REFRAG is a Meta AI **inference-efficiency decoding
framework** (arXiv:2509.01092, Sep 2025): retrieved chunks are compressed into a
single chunk-embedding each, fed into the **decoder** in embedding space, and a
lightweight RL policy selectively re-expands the relevant chunks to full tokens.
Gains: 30.85× time-to-first-token, 16× usable context, no accuracy loss. The notes
(§4.11) describe it exactly this way ("compress and filter context at the **vector
level**").

**Why it's out-of-scope here.** The core mechanism injects pre-computed chunk
*embeddings into the decoder* — impossible through managed provider APIs
(LiteLLM → GPT/Claude/Gemini). Text-space adaptations exist (keyword-compress the
low-priority 70% of chunks) but surrender most of the stated benefit and add
complexity. This is a Part 8 "LLM optimization (self-hosted inference)" technique,
the same category the audit already marks by-design out-of-scope.

**What industry uses for the underlying pain (retrieved context is irrelevant):**
- **CRAG — Corrective RAG** (arXiv:2401.15884): a lightweight *retrieval evaluator*
  grades retrieved documents; on low confidence it triggers corrective actions —
  re-retrieve with a refined query, fall back to web/knowledge search, or "refine"
  (decompose and re-query). Bolts onto any existing pipeline, no fine-tuning.
- **Self-RAG** (reflection tokens; needs fine-tuning) and **agentic/adaptive RAG**
  (query routing + iterative retrieval) — both absorbed into LangGraph/LlamaIndex
  agentic-RAG templates; that is now the standard production pattern.
- This repo already has reranking, `citation.py`, `critique.py`, and `evidence_gate`;
  CRAG closes the last gap (grade-then-correct) without self-hosting.

**Verdict.** ⛔ REFRAG → reclassified **[by-design out-of-scope]**. Replace the
candidate with **CRAG-style corrective retrieval**: a retrieval-quality evaluator
+ re-retrieve/refine/fallback branch inside the tool loop. That is the
production-verified improvement for the same pain.

---

## A3. Automated prompt-optimization loop (§6.15) — ✅ KEEP (validated, standard)

**The notes anchor is right.** §6.15 describes Opik's optimizer. Current state
(Opik optimizer v3.x, open source, self-hostable): six algorithms under one API —
Evolutionary (GEPA), FewShotBayesian, HRPO (root-cause synthesis), MetaPrompt,
ParameterOptimizer (Bayesian over temperature/top_p), plus **MCP tool-description
optimization**; optimizer chaining supported. Runs against *your* metric and *your*
dataset. This is a mature, usable fit.

**The ecosystem (per a June 2026 six-framework bake-off and vendor docs):**
- **DSPy (MIPROv2)** — the ecosystem standard, but you must adopt the full DSPy
  programming model; a framework, not a drop-in.
- **TextGrad** — textual "gradient" descent; LLM critiques output, proposes edits;
  you supply the loss. Often the slowest/least-cost-efficient.
- **GEPA** — standalone evolutionary/Pareto optimizer; good for complex/diverse
  solution spaces.
- **future-agi/agent-opt** — Apache-2.0, six optimizers behind one `optimize()`,
  scores any metric, **runs on LiteLLM** (this repo's driver) — lowest-integration
  option to evaluate.
- **Arize Prompt Learning / MLflow** — optimization inside observability/MLOps
  platforms; only worth it if you're already in that stack.

**The cross-cutting lesson from the 2026 bake-off:** the *metric* is the product —
optimizing against a slightly-wrong metric faithfully produces a prompt that games
it. Hold out a test set; watch for overfitting.

**Fit here.** This repo already owns the hard parts: golden-set regression
(`regression_service.py`), evaluator verdicts (`evaluation_logs`, arena/multi-turn),
and canary/shadow promotion (`experiments.py`). The loop = optimizer proposes prompt
variant → score on the golden set offline → promote the winner through the existing
canary path. Langfuse stays passive; Opik (or agent-opt) drives the offline loop.

**Verdict.** ✅ Keep as candidate, medium priority. Reuse existing golden set +
canary; adopt Opik optimizer or agent-opt rather than hand-rolling a single algorithm.

---

# Group B — ML-Systems notes candidates

## B1. Serving confidence gate (§5.9) — ✅ KEEP (mature field; build with calibration)

**Where the field is (2024–2026):**
- *Selective prediction / abstention* is a long-mature discipline (empirical risk →
  Chow's rule → conformal). For LLMs it converged on: **semantic entropy** (Kuhn
  et al.), **verbalized confidence** (Tian et al. "Just Ask for Calibration"),
  **sample-consistency / SelfCheckGPT**, retrieval/tool-agreement signals, and
  **conformal calibration** — ConU, SConU (ACL 2025), Quach et al. (conformal risk
  control), Su et al. "API Is Enough" (black-box), and **UniCR** (arXiv:2509.01455):
  evidence fusion → calibrated probability of correctness → risk-controlled refusal,
  in a few-lightweight-layer package that works on API-only models.
- **Production/Vendors:** Patronus **Lynx** (SOTA open-source hallucination-detection
  judge, 70B) and ConfidenceGuard; Galileo; plus every major eval platform ships a
  groundedness/hallucination check. Caveat that matters here: **93% of teams using
  LLM-as-a-judge report reliability issues** (Galileo industry data) — raw judge
  output is *not* a calibrated confidence; thresholds must be derived from a
  validation set.

**Fit here.** Repo has pre-dispatch guardrails only. Realistic black-box path:
compute a confidence composite (groundedness score from existing
`quality_service`/deepeval judge + retrieval-consistency + sampling agreement where
affordable), calibrate a threshold on logged verdicts/feedback (validation quantile
or lightweight conformal risk control), then refuse-or-hedge below threshold in the
plan/generation node.

**Verdict.** ✅ Keep, high value. Implement as *calibrated* threshold, not raw
self-report; expose an "uncertain" state instead of a fluent-but-wrong answer.

---

## B2. Popularity-bucketed feedback monitoring (§7.5) — ✅ KEEP (table stakes)

**What industry does.** Slice/group analysis is the *default* capability of 2026 LLM
observability: Arize (Phoenix slice analysis; marketed explicitly as "slicing by
prompt type, user segments, and feedback loops"), LangSmith (feedback + trace
filtering), WhyLabs, Datadog, Helicone, Braintrust, TrueFoundry. Aggregate KPIs
conceal failures in hot paths; **slice-level KPI + importance weighting (IPS)** is
the recsys/ML standard for exactly this degenerate-feedback-loop detection.

**Fit here.** The repo logs feedback (`sendMessageFeedback`) and has aggregate drift
(z-score/PSI). Adding popularity-decile × helpful-rate slice tables to the
observability viewer turns aggregate monitoring into slice monitoring for pennies —
same tables, one aggregation.

**Verdict.** ✅ Keep, low effort / high value. Add slice function over existing
`usage_logs`/feedback tables; IPS-correct the helpful-rate by query frequency.

---

## B3. Bandit exploration (§8.9) — ✅ KEEP, but upgrade the framing (contextual)

**Where the field is.** Bandits × LLMs is now an active research direction —
**ε-greedy** remains the deployed baseline (production talks show ε-greedy loops
with IPS-adjusted cost), and the SOTA is **contextual**: "Online Multi-LLM Selection
via Contextual Bandits" (Poon et al., AAAI 2026, first framework for unstructured
prompt dynamics), "Multi-Armed Bandits Meet LLMs" (arXiv:2505.13355), **PAK-UCB**
(adaptively selects the best generative model per prompt), and Explore-Commit-
Eliminate for offline-vs-online model selection.

**Design notes that research validates:** feedback is *missing-not-at-random* —
apply **IPS/weighting** to debias rewards by exposure; only move to bandits once
sliced feedback volumes (B2) exist; start ε-greedy over the existing HMAC buckets,
graduate to a per-prompt contextual arm/model choice later.

**Fit here.** `experiments.py` is a static weighted split — consistent with the
standard canary/shadow practice, not lagging. The upgrade is additive: ε-greedy
selection + IPS-corrected reward from feedback logs; later a contextual bandit over
`fast_chat`/`complex_reasoning` routing if latency-insensitive paths allow.

**Verdict.** ✅ Keep, medium priority, pair with B2. ε-greedy first, contextual later.

---

## B4. Fairness / interpretability surface (§1.4, §8.7) — ✅ KEEP, kept deliberately slim

**What industry does.**
- *Tooling:* Microsoft Responsible AI dashboard (error analysis + fairness +
  interpretability in one surface), Fairlearn, Aequitas, Fiddler (fairness
  monitoring), Credo AI (governance registry). These target tabular/classic ML;
  for LLM assistants the practical method is **slice-based evaluation** (score
  metrics by cohort/query-type/domain) plus **parity on operational signals**
  (guardrail block-rates, refusal rates).
- *Reality check:* Stanford AI Index 2026 (Responsible AI chapter) documents that
  *fairness measurement for generative AI is still immature* — no converged
  certification metric. Post-hoc fairness audits of LLMs route through bias
  benchmarks (BBQ, Winogender, truthfulQA subsets) and cohort-sliced evals.

**Fit here.** The repo records guardrail blocks and evidence-gate decisions but only
shows cohort-blind aggregates. The defensible scope: slice guardrail-block rate,
refusal rate, and eval verdicts by cohort/domain in the viewer; document known
limitations; do NOT attempt formal fairness metrics the industry itself hasn't
converged on.

**Verdict.** ✅ Keep, low-medium priority, deliberately scoped to slice evals +
block-rate parity + documented limitations.

---

## B5. Responsible-ML formalization (§8.7) — ✅ KEEP, highest strategic urgency

**Why now.** The EU AI Act is **fully applicable since 2 Aug 2026**; the AI Office
holds enforcement powers over GPAI from that date, with fines up to **€15M or 3% of
global turnover** (Art 101). Timeline that matters:
- GPAI *provider* obligations (Art 53: technical documentation, downstream-provider
  info, copyright policy incl. TDM reservations, training-content summary; Art 55
  systemic-risk: **red-teaming**, incident reporting, cybersecurity) — applicable
  since **2 Aug 2025** (models already on market: comply by 2 Aug 2027).
- This product consumes models via API ⇒ it is a **downstream provider/deployer**,
  *not* subject to Art 51–55; but it must (a) classify its system — an AI assistant
  chatbot is at worst **limited-risk (Art 50 transparency)**, not Annex III
  high-risk, (b) consume the provider's Annex XII docs into its own risk
  management, (c) keep evidence for that classification.
- **ISO/IEC 42001** is the certifiable AI-management-system standard; it is NOT
  equivalent to Art 17 QMS, but 42/44 mapped AI-Act duties have a corresponding
  clause — its evidence is *reusable*, which is exactly the "examinable surface" ask.

**Tooling landscape (2025–2026):** red-teaming is a product category now — PyRIT
(Microsoft), Garak (NVIDIA), Promptfoo, DeepTeam, Mindgard, Giskard. The repo's
`redteam_service` is already Garak-style, which is the right lineage.

**Fit here.** Build the admin **Responsibility/Audit** tab: prompt/model version
provenance per generation, retention coverage, last red-team run + findings, guardrail
block-rate trends, GDPR export/erasure evidence, and an EU AI Act classification
statement (downstream, limited-risk chatbot + consuming Annex XII docs). This turns
scattered compliance features into examinable, auditable posture.

**Verdict.** ✅ Keep, highest strategic value/cost ratio — matches an active
regulatory deadline, and the repo already owns ~80% of the underlying data.

---

# Recommended build order

| # | Item | Research verdict | Industry status | Effort | Priority |
|---|---|---|---|---|---|
| 1 | **CRAG-style corrective retrieval** (replaces §4.11 REFRAG) | 🔁→ keep-as-CRAG | Production standard (agentic RAG) | Small–Med | 🔴 High (accuracy) |
| 2 | **Confidence gate with calibrated threshold** (§5.9) | ✅ keep | Mature field (conformal/selective) | Med | 🔴 High (trust) |
| 3 | **Popularity-bucketed / sliced feedback monitoring** (§7.5) | ✅ keep | Table stakes (Arize/LangSmith…) | Small | 🔴 High (visibility) |
| 4 | **Responsible-ML audit surface** (§8.7) | ✅ keep | Regulated (EU AI Act live) | Med | 🟠 Med-High (compliance) |
| 5 | **Automated prompt optimization loop** (§6.15) | ✅ keep | Standard (Opik/DSPy/agent-opt) | Med–Large | 🟠 Med |
| 6 | **Schema-enforced structured output** (the real §2.3 fix) | 🔁→ keep | 2026 production standard | Small | 🟠 Med |
| 7 | **Bandit exploration, ε-greedy→contextual** (§8.9) | ✅ keep (reframed) | Emerging (AAAI'26) | Med | 🟡 Med (needs feedback volume) |
| 8 | **Fairness/interpretability slices** (§1.4/§8.7) | ✅ keep (slim scope) | Immature field | Med | 🟡 Low–Med |
| 9 | **Verbalized Sampling** (§2.3) | 🔁 demote | SOTA only for diversity | Small | ⚪ Low (not product-fit) |
| — | **REFRAG (§4.11)** | ⛔ reclassify | Self-hosted inference opt | — | ⛔ Out-of-scope |

# Sources
- Verbalized Sampling: arXiv:2510.01171 (Zhang et al., 2025) + CHATS-lab repo; CISC: Google, ACL 2025 Findings.
- REFRAG: Meta AI, arXiv:2509.01092 (2025) + Simulanics reference impl.
- CRAG arXiv:2401.15884; Self-RAG / agentic-RAG adoption (LangGraph/LlamaIndex templates, 2025 commentary).
- Prompt optimization: Opik optimizer SDK README & product docs (comet.com) v3.x; June 2026 six-framework comparison (DSPy/GEPA/TextGrad/agent-opt/Arize/MLflow); metaTextGrad (NeurIPS 2025); template-extraction benchmark (arXiv:2506.19773).
- Confidence/calibration: UniCR arXiv:2509.01455; SConU ACL 2025; Su et al. "API Is Enough" (EMNLP 2024 Findings); Kuhn et al. semantic entropy; Tian et al. verbalized confidence; uncertainty-distillation arXiv:2503.14749; Patronus Lynx/ConfidenceGuard; Galileo LLM-as-judge reliability data.
- Slice monitoring: Arize Phoenix slice analysis + TrueFoundry 2026 LLM-observability roundup.
- Bandits: Poon et al., AAAI 2026; arXiv:2505.13355; PAK-UCB (OpenReview); ECE (Berkeley).
- Fairness/RAI: Microsoft Responsible AI dashboard; Stanford HAI AI Index 2026 (Responsible AI chapter).
- Red-teaming: PyRIT, Garak, Promptfoo, DeepTeam, Mindgard (2026 roundups); agentic-era red-teaming survey (arXiv:2605.04019).
- EU AI Act: Regulation (EU) 2024/1689 consolidated text (July 2026); Commission implementation guidance (2026); GPAI Code of Practice (July 2025); Confir.eu GPAI provider-vs-downstream explainer; AIPolicyTracker ISO/IEC 42001 crosswalk; Digital Omnibus timelines.

---

# Appendix — Implementation status (candidate sweep)

All eight validated/replaced candidates shipped on `main` in one sweep (backend +
frontend + tests; no push). Implementation follows the verdicts above, with the
scope deliberately kept to record-only / fail-open surfaces where the research
flagged caution.

| # | Feature | Implementation | Notable scope cuts vs. verdict |
|---|---|---|---|
| 1 | CRAG-style corrective retrieval | `services/rag/retrieval_guard.py`; wired into `orchestrator/nodes.py` critic/grader re-retrieve loop | Retriever-grader grades existing citations; corrective action re-queries and only replaces results that strictly improve the mean score; revision budget from `CRAG_MAX_REVISIONS`, fail-open on RAG errors |
| 2 | Confidence gate with calibrated threshold | `services/confidence_service.py` (composite groundedness score, `calibrated_threshold`, `decide`) | Record-only decision stamped on the synthesizer state; never blocks or rewrites (fail-open by design) |
| 3 | Popularity-bucketed slice monitoring + fairness | `services/monitoring/slices_service.py` + `/admin/monitoring/slices` & `/fairness` | Fairness kept slim: population-level eval-pass-rate parity + provider error parity only (no protected attributes) |
| 4 | Responsible-ML audit surface | `services/audit_service.py` + `/admin/audit`, `/admin/audit/redteam`, `/admin/audit/eu-act`; GDPR export/erasure evidence via `audit_logs` | EU AI Act surfaced as an explicit classification statement with dates, not a compliance claim |
| 5 | Automated prompt-optimization loop | `services/prompt_optimizer.py` + `/admin/optimization/runs` & `/admin/optimization/run` (POST); every run persisted to `prompt_optimization_runs` | Proposer + judge injectable (LLM default, deterministic rubric); promotes only if a candidate beats baseline by the margin; failed runs recorded, never raise |
| 6 | Schema-enforced structured output | `response_format={"type":"json_object"}` passthrough on `litellm_client`; plan-service planner retries once with- repair, then heuristic fallback | Providers that can't honor the schema get the param dropped (`litellm.drop_params`) |
| 7 | Bandit exploration (ε-greedy) | `services/bandit_service.py` + `/admin/monitoring/bandits`; rewards fed from `record_feedback` thumbs into `bandit_rewards`; `prompt_variant` stamped on assistant message metadata | Selection only active when the experiment config declares `bandit: true`; contextual/IPS upgrade explicitly out of scope |
| 8 | Fairness/interpretability slices | Folded into the slice/fairness surface (item 3) | — |

New tables (migration `c1d2e3f4a5b6`): `bandit_rewards`, `prompt_optimization_runs`,
`redteam_runs`. Verdict items 9 (verbalized sampling) and REFRAG were **not**
implemented: verbalized sampling was demoted and its real fix (structured
output, item 6) shipped instead; REFRAG was reclassified out-of-scope (self-
hosted inference optimization) and replaced by item 1.