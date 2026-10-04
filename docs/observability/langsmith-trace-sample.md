# A real agent run, span by span

Captured 2026-10-04 from a real `orchestrator_graph` run against the hosted
stack. Regenerate with the command in `AGENTS.md` §9.26 -- the numbers below are
measured, not written by hand.

## Read this first: this trace is NOT in LangSmith

`LANGSMITH_TRACING=true` was set for the capture and the run produced all of
these spans, but **every ingest POST was rejected**:

```
POST https://api.smith.langchain.com/runs/multipart -> 403 {"error":"Forbidden"}
GET  https://api.smith.langchain.com/sessions       -> 403 Forbidden
```

Diagnosed, not guessed: the configured key is a **project token**
(`lsv2_pt_...`, not a `lsv2_sk_...` service key) and every *authenticated*
endpoint 403s, with and without a `project_name`. `/info` answers 200, but that
endpoint does not prove the token has workspace access, so the finding is
"this token cannot reach the workspace that owns `nexus-ai-assistant`", not
"the key is valid but read-only". That is a credential-scope problem and it has
to be fixed in LangSmith, not in code.

So `LANGSMITH_TRACING` is back to `false`: with the upload guaranteed to fail,
leaving it on only pays for rejected requests on every traced run.

What this file *is*: the same span tree LangSmith would have received, captured
from the `astream_events` payloads (each carries `run_id`, `parent_ids`, `name`).
Nothing here is simulated. What is missing is only the destination -- when the
token is fixed, this run is the baseline to compare the uploaded trace against.

## The run

| | |
|---|---|
| question | 'In one short paragraph, what problem does a per-run token budget solve that a per-node timeout does not?' |
| conversation | `86d8d9a5-db97-452e-b094-2388883d9cb5` |
| spans | 10 |
| wall clock | 10406ms |
| answer | 473 chars |
| token deltas streamed | True |

`token deltas streamed: True` -- this run streamed model deltas, so the answer below is the concatenation of `on_chat_model_stream` chunks.

## Span tree

```
- **LangGraph** — `-` — 10406ms — 7 events — `01a10582-0673-7e03-b815-ef57febb54b3`
  - **bootstrap** — `-` — 0ms — 2 events — `01a10582-0684-7383-92ab-7bf7d4f98e68`
  - **planner** — `-` — 3172ms — 2 events — `01a10582-0688-78a1-bceb-b5218e677650`
  - **route_after_planner** — `-` — 0ms — 1 events — `01a10582-12f0-7bb2-90e8-c8cf13a2b4d2`
  - **orchestrator** — `-` — 906ms — 2 events — `01a10582-12f8-7f23-816f-e9714a618c65`
  - **route_after_orchestrator** — `-` — 0ms — 1 events — `01a10582-167b-7bc0-9a3b-d37a3c74cc9e`
  - **critic_grader** — `-` — 2453ms — 2 events — `01a10582-167f-7281-a64a-1e38a9855479`
  - **route_after_critic** — `-` — 0ms — 1 events — `01a10582-2012-76f3-8803-760b27e0bd4d`
  - **synthesizer** — `-` — 2875ms — 2 events — `01a10582-2015-7122-bb3a-a3ad7f0000ad`
  - **artifact** — `-` — 969ms — 2 events — `01a10582-2b52-78e1-837e-6d7351c19915`
```

## Caveats on these numbers

- **Durations are consumer-side.** These event payloads carry no `timestamp`
  key, so each span's time is measured from first to last sighting of its
  `run_id` by the capture script. Real, but it includes the script's own
  handling, so read it as an upper bound.
- **The run-ids share a common prefix** because they are timestamp-ordered ids
  minted in the same millisecond window. They are distinct; the shared prefix is
  not a bug in the capture, and it is not a sign of deduplication either.
- **This run had no checkpoint durability.** It compiled with
  `langgraph_agent_workflow_compiled_with_inmemory_fallback`. The DSN is
  rewritten from the pooler's transaction port 6543 to session port 5432 for
  this one consumer, because the checkpointer holds a single long-lived
  connection; in this environment that session-mode connection was closed by
  the pooler (`server closed the connection unexpectedly`), so
  `ResilientPostgresSaver` degraded to `InMemorySaver` — precisely the seam it
  exists for. Node structure and timings are unaffected; what is missing is
  survival across a restart. See AGENTS.md §9.26.

## Answer produced by this run

A per‑run token budget caps the total number of language‑model tokens an entire execution can emit, guaranteeing a hard upper bound on overall cost, latency, and storage regardless of how many steps are taken; in contrast, a per‑node timeout only limits the duration of individual steps and cannot prevent a flurry of short‑lived nodes from collectively producing an unbounded amount of output, so it fails to protect against runaway token consumption across the whole run.

## To get a real LangSmith trace

1. In LangSmith, issue a **service key** (`lsv2_sk_...`) for the workspace that
   owns the `nexus-ai-assistant` project, or grant the existing `lsv2_pt_`
   project token write access to `/runs` and `/sessions`.
2. Put it in `backend/.env` as `LANGSMITH_API_KEY` and set
   `LANGSMITH_TRACING=true`.
3. Confirm the upload is accepted before trusting the dashboard -- a run that
   silently 403s looks exactly like a run that produced no spans:

```bash
# from backend/
uv run python -c "import os,urllib.request;urllib.request.urlopen(urllib.request.Request('https://api.smith.langchain.com/sessions?limit=1',headers={'x-api-key':os.environ['LANGSMITH_API_KEY']}))"
```

If that raises `HTTPError 403`, tracing is still blocked no matter what the
dashboard shows.
