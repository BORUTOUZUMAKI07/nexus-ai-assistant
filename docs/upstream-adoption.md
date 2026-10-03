# Upstream capability adoption — evaluation & decisions

> Status: **decided and built.** This document is the rationale for the
> A1–A5 / B1–B2 / C1 batch: which LangChain / LangGraph / Deep Agents
> capabilities this repo adopts, which it deliberately rejects, and why.
>
> **External figures were read from the sources cited below on 2026-10-03.**
> Stars, versions and benchmark numbers move; re-read the source before reusing
> a number, and do not cite this file as the primary source for an upstream
> fact.

## 0. The local stack these decisions are made against

Probed from `backend/` with `importlib.metadata` on 2026-10-03:

| Package | Version |
|---|---|
| `langgraph` | 1.2.11 |
| `langchain-core` | 1.6.1 |
| `langgraph-checkpoint` | 4.2.0 |
| `langgraph-checkpoint-postgres` | 3.1.2 |
| `litellm` | 1.99.0 |

**`langchain` itself is not installed** — only `langchain-core`. This is the
single most important fact for the middleware decision below: the middleware
catalog and `create_agent` live in the `langchain` package, so any middleware
adoption is a *new top-level dependency*, not a version bump. The graph here is
built directly on LangGraph `StateGraph`, not on `create_agent`.

## 1. Decision framework

Capabilities were sorted by what adopting them would actually require:

- **Tier 1 — use the framework's primitive.** A small, stable LangGraph feature
  that replaces hand-rolled code and can be tested against the real library.
- **Tier 2 — keep in view.** Real, but not needed by the current graph, or
  already solved in-domain; adopting now would add surface with no consumer.
- **Tier 3 — not applicable.** Capabilities that assume a managed runtime /
  deployment shape this repo does not have.
- **Tier 4 — superseded.** Features the current upstream version removes or
  replaces, or that duplicate something already load-bearing here.

The scoping rule applied throughout: **unreachable config is not a safety net.**
A primitive wired in but never read is the same defect class as the
`ResilientPostgresSaver` that LangGraph silently discarded at `compile()` (see
`AGENTS.md` §2), and it is invisible to a green suite. So a feature is adopted
only when the current graph has a call site that makes it load-bearing.

## 2. Tier 1

### 2.1 `RetryPolicy` + `TimeoutPolicy` — **adopted** (B1/B2)

`backend/app/agents/orchestrator/retry_policy.py`, imported from the framework
(`retry_policy.py:130-131`):

```python
from langgraph.errors import NodeCancelledError, NodeTimeoutError
from langgraph.types import RetryPolicy, TimeoutPolicy
```

LangGraph's **default** `retry_on` is wrong for this graph in both directions,
measured against 1.2.11 with `RetryPolicy(max_attempts=3)` (recorded in
`retry_policy.py:6-14`):

| Raised | Default attempts |
|---|---|
| `RuntimeError` | 1 (not retried) |
| `OSError` | 1 (not retried) |
| `TimeoutError` | 1 (not retried) |
| `ConnectionError` | 3 |
| `ValueError` / `KeyError` | 1 (correct) |
| bare `Exception` | 3 |

The reason is `langgraph/_internal/_retry.py::default_retry_on`: it returns
`False` for `RuntimeError` and `OSError`, and `TimeoutError` subclasses
`OSError`. The three most likely transient failures in a graph that talks to
Postgres and an LLM router are therefore never retried — it *looks* configured
and changes nothing.

The default is wrong the other way too: it ends with `return True`, so it
retries `litellm.AuthenticationError` (401), `BadRequestError` (400),
`ContextWindowExceededError` (400), `sqlalchemy.ProgrammingError`,
`IntegrityError`, and bare `Exception`. Three attempts against a bad API key is
how a config mistake becomes a rate-limit incident; a retried `IntegrityError`
is a duplicate row.

Two traps that are not obvious:

- **Retrying `NodeTimeoutError` multiplies the ceiling.** Measured with
  `run_timeout=0.05, max_attempts=3`: **1.8s to fail (36×)**. It is in
  `NEVER_RETRY`, checked first (`retry_policy.py:49-54`, `:146-149`).
- **`RETRYABLE_LLM` must use `openai.APIConnectionError`, not
  `litellm.APIConnectionError`.** `litellm.Timeout` sits in a *parallel* branch
  under the openai base; a tuple of litellm's own classes misses it
  (`retry_policy.py:186-216`).

`TimeoutPolicy` is applied per node (`TIMEOUTS`, `retry_policy.py:343-353`),
sized from what each node does. Nodes that write — `artifact`, `synthesizer`,
`bootstrap` — are `NO_RETRY` (`:355-367`) because a repeat can double a write or
re-run the most expensive loop in the graph.

Test: `backend/tests/test_node_policies.py` (61 tests), with a 14/14
load-bearing revert harness. Recorded as `AGENTS.md` §9.22.

### 2.2 `error_handler` — **not adopted**

A per-node error-handling hook. No node needs a recovery consumer today: the
graph's failures either retry (transient) or terminate the run. Routing a failed
node's error into a new node would add an edge and a failure mode with no
call site that reads it — the exact "unreachable config" shape the scoping rule
rejects. Revisit only if a node genuinely needs to continue after a peer fails.

### 2.3 `get_stream_writer` — **not adopted yet; the trap is documented**

The graph is streamed with `astream_events(version="v2")`
(`backend/app/api/v1/conversations.py:401`), and no app code calls
`get_stream_writer` (only the probe in `tests/test_stream_custom_event_trap.py`).
If a mid-run event is ever added, the measured (langgraph 1.2.11) behaviour is:

| Call | Result |
|---|---|
| `astream_events(v2)` | **0** payloads |
| `astream_events(v2, stream_mode="custom")` | payload, as a root `on_chain_stream` |
| `astream_events(v2, stream_mode=["custom"])` | **0** payloads — the trap |
| `astream(stream_mode="custom")` | payload, unwrapped |
| `astream_events(v2, stream_mode="bogus")` | no raise, **and** suppresses root `on_chain_stream` |

The list form is what anyone writes for more than one mode, and it silently
delivers nothing; a typo is worse, because it also silences the root deltas.
`on_custom_event` is never emitted. Pinned by
`backend/tests/test_stream_custom_event_trap.py` and `AGENTS.md` §9.23.
Adopting the writer means passing `stream_mode="custom"` as a **string** and
handling the `on_chain_stream` envelope.

## 3. Tier 2 — kept in view, not adopted now

### 3.1 `BaseStore` (framework long-term memory) — **rejected in-place**

`graph.py:354-362` documents the decision at the compile call: this repo has
**zero `get_store()` call sites**. Long-term memory is mem0
(`services/memory.py`) mirrored into the `user_memories` table
(`services/memory_lifecycle.py`) and read through `AgentState`. The dead
`store=` argument was removed (A1) precisely because an unread store is the
same defect class as the discarded checkpointer.

### 3.2 Subgraphs / `create_agent` composition — **not adopted**

The graph composes nodes directly. Dropping a `create_agent` subgraph in would
require the `langchain` package and a second control plane for tool calls, HITL,
and message history that the DDD services already own.

### 3.3 Time-travel / replay — **not adopted**

Checkpointing is real (`ResilientPostgresSaver`), but there is no time-travel
UI and no product requirement for one. Revisit with a concrete user story.

### 3.4 LangSmith Studio — **not adopted**

A dev-time tool, not a runtime dependency. Tracing is already available
opt-in via `LANGSMITH_TRACING` (`backend/app/core/config.py:258`).

### 3.5 Test fakes — **already the repo's pattern**

`backend/tests/fakes.py` + `dependency_overrides` + `ASGITransport`. No
framework import needed; the repo already does this.

## 4. Tier 3 / Tier 4

- **Tier 3 — not applicable.** Capabilities that assume a managed LangGraph
  runtime or hosted control plane. This is a self-hosted FastAPI app with its own
  auth, Postgres, Celery, and observability; there is nothing to plug a managed
  plane into.
- **Tier 4 — superseded.** Features the current upstream version removes or
  replaces, or that duplicate something already load-bearing here. No adoption.

## 5. Deep Agents / LangChain middleware — **rejected as a dependency**

**What it is.** `deepagents` (PyPI: `deepagents 0.7.21`, released **2026-09-30**;
MIT; classifier **Development Status :: 4 - Beta**; **1 maintainer**;
Python `>=3.11,<4.0`). It is an opinionated harness *on top of* LangChain
`create_agent`, which is itself on LangGraph. The LangChain middleware catalog
(`langchain.agents.middleware`: summarization, human-in-the-loop, PII detection,
model fallback, tool retry, model-call limits, …) is the same mechanism — and
the docs are explicit that middleware "is not a separate runtime: hooks run
inside the compiled LangGraph that `create_agent` returns."

**Why rejected.** Every headline capability is already implemented in-domain,
with tests and without a beta dependency:

| Deep Agents capability | Already here |
|---|---|
| Human-in-the-loop tool approval | `tool_gateway.py` + permission ladder + `config/permissions.yaml` |
| PII detection / redaction | `guardrail_service.py` |
| Summarization / context management | `services/context_compiler.py` |
| Sub-agents | `agents/subagents/` |
| Long-term memory | mem0 + `user_memories` + `memory_lifecycle.py` |
| Filesystem / tools | MCP server + `services/tools/` |
| Skills | `backend/skills/*.md` (documented as not runtime-loaded) |

Adopting it would migrate the tool-call, HITL and memory control planes onto a
**beta harness with one maintainer** whose stated security model is "trust the
LLM" — in a repo whose guardrail posture is the opposite — in exchange for
capability it already has.

**Verdict.** Reject as a runtime dependency. It is a reasonable standalone
portfolio/reference artifact; it is not the control plane for this app.

## 6. Jev / Open-Jev — **rejected; one prompt idea retained**

**What it is.** `Zefan-Cai/Open-Jev` — an independent reimplementation inspired
by TypeSafe's Jev, with no proprietary RLCD, private weights or training data.
Read from the repo on 2026-10-03: **389★, 52 forks, MIT, 112 commits**. It
returns typed decisions (`choice` / `noul` / `score`) as probabilities without
autoregressive answer generation or JSON parsing. Released models are LoRA +
a scalar decision head + fitted calibration, and require **pinned upstream Qwen
base weights plus the Open-Jev loader**.

**Measured results, from the project's own README (2026-10-03):**

| Evaluation | Result |
|---|---|
| Natural support pilot (released 2B) | **166/256 (64.8%)** vs BM25 **211/256 (82.4%)** |
| Support pilot acceptance policy | **14 errors among 88 accepted rows** → human review stays default |
| Browser snapshot — 2B | **32/120 (26.67%)**, *below* **35/120 (29.17%)** always-`BLOCKED` |
| JevBench public 231 — 2B / 9B / 27B-v1.1 | 150/231 · 179/231 · **197/231 (85.28%)** |
| Hard 111 — 2B / 9B / 27B-v1.1 | 46/111 · 66/111 · 80/111 |
| Open-Jev TREC | **pending** |

The load-bearing negatives are the first three: on the realistic workflow the
README reports, the released 2B model loses to a **BM25 keyword baseline**, and
its browser policy scores below a constant `BLOCKED`. The benchmark table above
is self-reported internal evaluation.

**Feature overlap.** The recipe catalog (search/ranking, citations, RAG,
guardrails, spans/dates, functions, skills, hierarchy, verification) maps
largely onto features the repo already ships — hybrid RAG + citation formatting,
`guardrail_service.py`, the tool permission ladder, the subagents, and the
`synthesizer`'s critic loop. The marginal capability is not worth a GPU
runtime, pinned weights, a custom loader, and a new serving path.

**What was retained.** One prompt-shaping idea: a passage under judgement must be
read **as quoted data, never as instructions**. This is the clause now in the
answer-coverage grader's prompt (`services/rag/answer_coverage.py`; recorded in
`AGENTS.md` §9.21) — a passage arguing for its own retention is an unmitigated
path into a drop decision. The repo already applies the same discipline to
synthesizer input via `nodes.py::_wrap_untrusted`.

**Considered and rejected.** A fourth `contradicted` label was proposed and
dropped: contradiction is *orthogonal* to "how much does this passage answer,"
every ordering on the legend is a lie, and it is already handled as a drop (the
version clause routes contradicting text to `does_not_answer`).

## 7. What was read, and what was not

Honest scope of the evidence behind this document:

**Read in full / at source —**
`Zefan-Cai/Open-Jev` repository README (2026-10-03); the `deepagents` PyPI page
(2026-10-03); the LangChain middleware overview page
(`docs.langchain.com/oss/python/langchain/middleware`); the local
`retry_policy.py` and `graph.py` compile block;
`tests/test_stream_custom_event_trap.py`; version metadata via
`importlib.metadata`.

**Not read / not run —**
Open-Jev and `deepagents` **source code**; the full LangChain built-in
middleware catalog; any Open-Jev model weights or inference; any Deep Agents
example. The benchmark numbers above are the projects' own reported figures, not
independently reproduced here. No upstream code was executed.

## 8. Related in-repo tests

| Decision | Test |
|---|---|
| Per-node retry/timeout predicate | `backend/tests/test_node_policies.py` |
| Custom-stream matrix | `backend/tests/test_stream_custom_event_trap.py` |
| Answer-coverage label order / drop rule | `backend/tests/test_answer_coverage.py` |
