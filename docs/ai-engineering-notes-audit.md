# AI Engineering Notes — Feature Harvest Audit

Maps every section of `AI_Engineering_Complete_Notes.md` (Part 1–11 + Appendix) to the
project's actual implementation. Verdicts:

- **[IMPLEMENTED]** — a real feature in this repo covers the notes' point(s).
- **[OUT-OF-SCOPE]** — by-design not built here: provider-managed inference (LiteLLM),
  no self-hosted GPU serving, no model training infra. Reference/knowledge content.
- **[CANDIDATE]** — the concept is useful for this product and is NOT currently built.
- **[N/A]** — pure reference/theory/glossary with no direct product mapping.

Audit date: 2026-09-27. Evidence is real file paths; nothing below is invented.

---

## Legend — where the evidence lives
| Notes topic | Project counterpart |
|---|---|
| Model calls | `backend/app/infrastructure/ai/litellm_client.py` (`completion`) |
| Agent orchestration | `backend/app/agents/orchestrator/nodes.py` + `graph.py` + `tot_node.py` |
| Subagents | `backend/app/agents/subagents/{researcher,critic,coder}.py` |
| RAG stack | `backend/app/services/rag/` (base, chunking, retrieval, reranking, query_rewriter, citation, critique, ingest) |
| Memory | `backend/app/services/memory.py` (mem0) |
| MCP | `backend/app/mcp/server.py` (server) + client (`.agents/mcp_config.json`) |
| Evaluation | `backend/app/services/evaluation/{deepeval,ragas,redteam,regression,quality,guardrail}_service.py` |
| Observability | `backend/app/services/observability/{viewer,tracing,metrics,cost_tracking}.py` |
| Tool governance | `backend/app/services/tools/tool_gateway.py` + `evidence_gate.py` |

---

# PART 1 — LLMs

| Section | Verdict | Evidence / note |
|---|---|---|
| 1.1–1.6 What an LLM is, architecture, training stages, decoding | **[OUT-OF-SCOPE]** | Provider-managed models via LiteLLM — no local training/serving intrinsics. |
| 1.7 The 7 generation parameters | **[IMPLEMENTED]** | Per-call `temperature`/`max_tokens` across `nodes.py`, `subagents/*.py`, `tot_node.py`; `DEFAULT_MODEL` in `core/config.py`. |
| 1.8 Decoding strategies (greedy/sampling) | **[IMPLEMENTED]** | Routing mixes `temperature=0.0` (router/ARQ) vs sampled (generation) — see `nodes.py` call sites. |
| 1.9 Distillation | **[OUT-OF-SCOPE]** | No model training. |
| 1.10 Running LLMs locally (Ollama etc.) | **[CANDIDATE-part]** | `ollama` is a lock dependency and LiteLLM can route to a local endpoint; no local model wiring in config/app yet. |
| 1.11 Transformer vs MoE | **[OUT-OF-SCOPE]** | Model-selection concern; `complex_reasoning`/`fast_chat` model aliases exist in routing. |

# PART 2 — Prompt Engineering

| Section | Verdict | Evidence / note |
|---|---|---|
| 2.1 What is prompt engineering | **[N/A]** | System prompt lives in `prompt_compiler.py` / `context_compiler.py`. |
| 2.2 Reasoning techniques + **ARQ** | **[IMPLEMENTED]** | ARQ-style structured reasoning query in `nodes.py::_run_arq` (forced auditable checks). |
| 2.3 Verbalized Sampling | **[CANDIDATE]** | Not implemented; could improve reasoning output extraction. |
| 2.4 JSON prompting | **[IMPLEMENTED]** | Strict-JSON plan parsing in `services/plan_service.py`; `services/structured_output.py`. |

# PART 3 — Fine-Tuning

| Section | Verdict | Evidence / note |
|---|---|---|
| 3.1–3.9 PEFT/LoRA/GRPO/datasets/ART | **[OUT-OF-SCOPE]** | No training infrastructure — models are fetched/upgraded upstream. |

# PART 4 — RAG

| Section | Verdict | Evidence / note |
|---|---|---|
| 4.1–4.4 RAG + vector vs purpose | **[IMPLEMENTED]** | Qdrant-backed embeddings (`infrastructure/vector/qdrant_client.py`) + hybrid retrieval (`services/rag/retrieval.py`), ingest, query rewriting, child→parent. |
| 4.5 5 chunking strategies | **[IMPLEMENTED]** | `services/rag/chunking.py` — markdown header-aware semantic chunking with token boundaries + 32-token child overlap. |
| 4.6 Prompting vs RAG vs fine-tuning framework | **[N/A]** | Decision doc; product uses RAG + memory. |
| 4.7 8 RAG architectures | **[IMPLEMENTED-part]** | Basic + agentic RAG covered (orchestrator subagents). |
| 4.8 RAG vs agentic RAG | **[IMPLEMENTED]** | Researcher subagent + tool loop acting on retrieval results. |
| 4.9 HyDE | **[IMPLEMENTED]** | Conditional HyDE in `services/rag/query_rewriter.py` + `base.py`. |
| 4.10 Fine-tune vs LoRA vs RAG | **[N/A]** | Reference comparison. |
| 4.11 REFRAG | **[CANDIDATE]** | Not implemented. |
| 4.12 CAG (cache-augmented generation) | **[IMPLEMENTED]** | `infrastructure/cache/cag_service.py` + `prompt_compiler.py` CAG variant. |
| 4.13 RAG → agentic RAG → memory | **[IMPLEMENTED]** | mem0 long-term memory (`services/memory.py`) injected into agent system prompt. |

# PART 5 — Context Engineering

| Section | Verdict | Evidence / note |
|---|---|---|
| 5.1–5.3 Context types/stages | **[IMPLEMENTED]** | SystemPromptCompiler + per-user `system_prompt_override` + memory injection. |
| 5.4 Multi-source research workflow | **[IMPLEMENTED]** | Researcher subagent + web_search + RAG retrieval assembly. |
| 5.5 Context engineering in skills | **[IMPLEMENTED]** | `backend/skills/{coder,critic,researcher}.md` loaded by orchestration layer. |
| 5.6 Manual vs agentic context | **[IMPLEMENTED]** | Agentic orchestration graph owns context assembly. |

# PART 6 — AI Agents

| Section | Verdict | Evidence / note |
|---|---|---|
| 6.1–6.6 Building blocks, memory types, design patterns | **[IMPLEMENTED]** | Orchesterer + subagents + HITL approvals + mem0 memory + tool gateway. |
| 6.7 ReAct | **[IMPLEMENTED]** | Agentic loop: tool call → result → continue, in `nodes.py` (pending_tool_calls + tool results). |
| 6.8 5 levels of agentic AI | **[IMPLEMENTED]** | Multi-level: tools, subagents, planner (`plan_service.py`), memory. |
| 6.9 Glossary | **[N/A]** | Reference. |
| 6.10 4 layers | **[N/A]** | Reference. |
| 6.11 7 multi-agent patterns | **[IMPLEMENTED-part]** | Orchestrator–worker + supervisor-style graph; not every listed pattern. |
| 6.12 A2A protocol | **[OUT-OF-SCOPE]** | External agent interconnect protocol; not needed for this product. |
| 6.13 AG-UI protocol | **[IMPLEMENTED]** | `tool_call`/`tool_result` SSE events with AG-UI-style `messageId`/`tool`/`input`/`output` aliases in `api/v1/conversations.py`. |
| 6.14 Agent protocol landscape | **[N/A]** | Reference. |
| 6.15 Opik automated prompt optimization | **[CANDIDATE]** | Langfuse is wired optionally; automated prompt-optimization loop is not. |
| 6.16 Deployment strategies | **[OUT-OF-SCOPE-part]** | CI/CD live gates exist; per-model deployment orchestration is not (managed providers). |

# PART 7 — MCP

| Section | Verdict | Evidence / note |
|---|---|---|
| 7.1–7.8 Host/client/server, capabilities, `mcp-use` | **[IMPLEMENTED]** | FastMCP server (`mcp/server.py`) with tools (web_search, execute_python_code, memory, create_artifact…), resources, prompts, elicitations; exposed as an authenticated SSE/session endpoint (`MCP_API_KEY`, `MCP_SERVER_NAME` in `core/config.py`); client (`mcp/client.py`) discovers server tools and translates them to OpenAI function-calling schema; SSE event forwarding. |

# PART 8 — LLM Optimization (inference)

| Section | Verdict | Evidence / note |
|---|---|---|
| 8.1–8.4 Compression (distillation/pruning/low-rank/quantization), continuous batching, KV-cache, prefill-decode, sharding | **[OUT-OF-SCOPE]** | Managed inference (LiteLLM → provider). No self-hosted GPU serving. Model choice is config-level only. |

# PART 9 — LLM Evaluation

| Section | Verdict | Evidence / note |
|---|---|---|
| 9.1 Why evaluation | **[N/A]** | Motivation. |
| 9.2 G-Eval | **[IMPLEMENTED]** | `evaluation/deepeval_service.py` (groundedness+informativeness fallback). |
| 9.3 Arena-as-a-judge | **[IMPLEMENTED]** | `evaluate_arena_pair` + `/admin/evaluation/arena`. |
| 9.4 Multi-turn evals | **[IMPLEMENTED]** | `evaluate_conversational` + `/admin/evaluation/conversational`. |
| 9.5 MCP-powered app evals | **[IMPLEMENTED-part]** | Component evals + prompt-regression gates; dedicated MCP-tool eval suite not separate. |
| 9.6 Component-level evals | **[IMPLEMENTED]** | `quality_service.py` / `ragas_service.py` (faithfulness/relevancy). |
| 9.7 Red teaming | **[IMPLEMENTED]** | `evaluation/redteam_service.py` (Garak-style probes) + `POST /admin/evaluation/redteam`. |

# PART 10 — LLM Deployment

| Section | Verdict | Evidence / note |
|---|---|---|
| 10.1–10.3 vLLM, LitServe | **[OUT-OF-SCOPE]** | No self-hosted inference engines. |

# PART 11 — LLM Observability

| Section | Verdict | Evidence / note |
|---|---|---|
| 11.1 Eval vs observability | **[N/A]** | Reference framing. |
| 11.2 Opik-style tracking | **[IMPLEMENTED]** | Langfuse optional callbacks (`litellm_client._configure_langfuse`) + self-contained viewer (`observability/viewer.py`), cost/usage/drift/eval scorecard + JSON. |

# APPENDIX (cheatsheets A–F)

| Item | Verdict | Evidence / note |
|---|---|---|
| A–E decision selectors | **[N/A]** | Reference. |
| F inference-serving checklist | **[OUT-OF-SCOPE]** | vLLM-specific items (PagedAttention, prefill-decode…) not applicable to LiteLLM-managed routing; non-vLLM items (red-teaming, multi-turn+MCP evals, observability) ARE implemented. |

---

# Summary

| Verdict | Count (by section) |
|---|---|
| **[IMPLEMENTED]** | ~28 |
| **[CANDIDATE]** | 4 (1.10 local models, 2.3 verbalized sampling, 4.11 REFRAG, 6.15 prompt optimization) |
| **[OUT-OF-SCOPE] / [N/A]** | remainder (theory, training, inference-serving, glossaries) |

**Missing candidates genuinely useful for this product (not currently built):**
1. **Verbalized Sampling** (2.3) — could improve plan/reason extraction in `plan_service`.
2. **REFRAG** (4.11) — RAG-faithfulness reranking; could plug into `reranking.py`.
3. **Automated prompt optimization loop** (6.15) — Langfuse is passive; an Opik-style optimizer isn't wired.

Copyright caveat: two big Appendices (inference checklist rows) are vLLM-specific and out of product scope —
harvesting "all useful things" must exclude the self-hosting parts by design.