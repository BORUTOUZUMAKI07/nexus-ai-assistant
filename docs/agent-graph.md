# The agent graph — nodes, edges, state, routing

The orchestrator is an explicit LangGraph graph, not a prompt chain. This page
is the map: what each node owns, which way execution can go, and — the part that
is invisible in the code — **how shared state merges**.

Source of truth is [`backend/app/agents/orchestrator/graph.py`](../backend/app/agents/orchestrator/graph.py)
(structure) and [`retry_policy.py`](../backend/app/agents/orchestrator/retry_policy.py)
(per-node retry/timeout). If this page disagrees with them, they win.

Why this document exists at all: a graph's failure modes are *structural*
(where does a failure go back to? who owns the target? which fields accumulate?),
and none of those are readable from a 480-line file in one pass. Drawing the
graph is how the decisions stop being implicit.

---

## 1. Nodes

Nine nodes. Every one is registered through `_add()`, which resolves its retry
policy and timeout from the node's own name — so a node cannot ship without a
ceiling (`timeout_for` raises `KeyError` for an unknown name).

| Node | Responsibility | Writes to shared state |
|---|---|---|
| `bootstrap` | Per-turn setup: context, memory, spend meter seeding | system prompt, memories, spend counters |
| `planner` | Decide the execution shape (incl. Fast-mode short circuit) | `plan`, `mode`-dependent routing inputs |
| `tree_of_thoughts` | Multi-candidate reasoning for a decomposed request | `tot_candidates`, `tot_synthesis` |
| `orchestrator` | ARQ flags, subagent dispatch, tool planning | `arq_flags`, `subagent_dispatches`, `pending_tool_calls` |
| `subagent_dispatcher` | Run researcher / coder / critic subagents | `subagent_outputs`, `pending_tool_calls` |
| `tool_node` | Execute planned tools through the gateway | `tool_results`, `pending_tool_calls` |
| `critic_grader` | RAG relevance gate (CRAG) → may demand a web search | `rag_relevance_score`, `grader_verdict`, `needs_web_search` |
| `synthesizer` | Compose the answer; owns the bounded critic-revision loop | `messages`, `revision_count`, `critique`, `answer_model` |
| `artifact` | Persist the finalized answer as a document + version | `artifact_*` |

`artifact` runs **after** the synthesizer and never before: the artifact body is
the post-critique, post-guardrail text, so building it from an earlier draft
would produce a different document from the one the user reads.

## 2. Edges and routing

```
START → bootstrap → planner
planner ──(route_after_planner)──┬→ tree_of_thoughts → orchestrator
                                 ├→ orchestrator
                                 └→ synthesizer                 (Fast mode)

orchestrator ──(route_after_orchestrator)──┬→ subagent_dispatcher
                                           ├→ tool_node
                                           ├→ critic_grader
                                           └→ synthesizer        (safety stop)

subagent_dispatcher ──(route_after_subagent)──┬→ tool_node
                                              └→ synthesizer

critic_grader ──(route_after_critic)──┬→ tool_node
                                      └→ synthesizer

tool_node → synthesizer → artifact → END
```

The routing rules are the actual contract — each is a small pure function over
state, in priority order:

| Router | First match wins |
|---|---|
| `route_after_planner` | `mode == "fast"` → `synthesizer`; `plan` present → `tree_of_thoughts`; else `orchestrator` |
| `route_after_orchestrator` | `arq_flags.safety_flag` → `synthesizer`; `subagent_dispatches` → `subagent_dispatcher`; `pending_tool_calls` → `tool_node`; else `critic_grader` |
| `route_after_subagent` | `pending_tool_calls` → `tool_node`; else `synthesizer` |
| `route_after_critic` | `arq_flags.safety_flag` → `synthesizer`; `needs_web_search` or `pending_tool_calls` → `tool_node`; else `synthesizer` |

**The safety flag is re-checked on every route, not only where it was first
observed.** It is a hard stop meaning "answer with no external tools". Checking
it only in the orchestrator let the critic set `needs_web_search` one hop later
and dispatch the exact tool the flag exists to prevent.

**Tree of Thoughts is a reasoning step, not a shortcut.** It feeds the
orchestrator rather than the synthesizer; routing it straight to synthesis is
how a planned "search the web, then summarise" request never touched the web
tool.

## 3. Shared state — the merge rules

This is the part that bites. `AgentState` is a `TypedDict`; the graph merges a
node's returned dict into state **key by key**.

- **`messages` has a reducer** — `Annotated[Sequence[BaseMessage], add_messages]`
  appends and de-duplicates by message id. Nothing else has one.
- **Every other field is last-write-wins.** Returning `{"tool_results": [x]}`
  *replaces* the list; it does not append.

So any field meant to accumulate must be read-then-extended **inside the node**,
which is what the code does (`tool_results = list(state.get("tool_results", []))`
before returning). When adding a node that contributes to an existing collection:

```python
# correct: read, extend, return the whole collection
results = list(state.get("tool_results", []))
results.extend(new_results)
return {"tool_results": results}

# wrong: silently discards every earlier entry
return {"tool_results": new_results}
```

If you add a genuinely new accumulating field, give it a reducer in
`state.py` instead of spreading read-then-extend across several nodes — one
declared merge rule beats N conventions that each have to be remembered.

## 4. Budgets and ceilings

Loops are individually bounded (critic revisions, research sub-queries, scrape
budget, model calls) and **their product is not**, so the run carries a
cumulative ceiling in `core/spend.py`: `AGENT_LOOP_MAX_STEPS` and
`AGENT_LOOP_TOKEN_BUDGET`. Those counters live in graph state rather than only
in a contextvar because the graph can suspend for HITL approval and resume in a
later request, where in-memory accounting is gone.

A budget stop must cost a **revision**, never the answer: the synthesizer tracks
`last_draft` separately from `response_text`, because `response_text` is only
assigned on the paths that accept a draft.

## 5. Checkpointing

State is persisted per thread by `ResilientPostgresSaver` (subclass of
`BaseCheckpointSaver` — LangGraph validates this with `isinstance` at compile
time and silently falls back to `InMemorySaver` when the check passes nothing).
The DSN resolves in this order: `LANGGRAPH_CHECKPOINT_DSN`, then `DATABASE_URL`,
with a transaction-mode pooler port (`6543`) rewritten to session mode (`5432`)
because the checkpointer holds one long-lived connection that a transaction-mode
pooler is allowed to close mid-run.

## 6. When this graph is the wrong tool

A graph is justified by **branches and rollbacks**, not by step count. A 20-step
linear pipeline is a workflow. Applying the criteria honestly:

- independent parallel work units — **not exploited today** (subagents dispatch
  sequentially),
- branch/rollback paths — **yes** (critic → tool → synthesizer; safety stop),
- intermediate state worth saving — **yes** (checkpointed runs, rejoin),
- per-node verifiable done — **partially** (critic rubric, evidence gate),
- coordination benefit > cost — **yes** for research/code turns, **no** for Fast
  mode, which is exactly why Fast mode bypasses the graph in one hop.

That last row is the useful one: Fast mode is the admission that most turns do
not need a graph, and it is one edge, not a second pipeline.
