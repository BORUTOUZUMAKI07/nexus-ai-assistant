# A real agent run, span by span

Captured 2026-10-04 from a real `orchestrator_graph` run against the hosted
stack. The numbers below are measured, not written by hand.

There is no regeneration script for this file, and there never was one — the
capture was an ad-hoc script run once and not kept. (An earlier version of this
header pointed at "the command in `AGENTS.md` §9.26"; §9.26 contains no such
command, so the pointer was dangling.) Re-deriving the tree means instrumenting
`services/run_executor.py`'s `astream_events` payloads again, which is the same
mechanism LangSmith itself consumes.

## Read this first: this capture never reached LangSmith

The 2026-10-04 capture below was made with `LANGSMITH_TRACING=true` and the run
produced all of these spans, but **every ingest POST was rejected**:

```
POST https://api.smith.langchain.com/runs/multipart -> 403 {"error":"Forbidden"}
GET  https://api.smith.langchain.com/sessions       -> 403 Forbidden
```

### What actually caused it — corrected 2026-10-05

**An earlier version of this section blamed the key.** It said the configured
credential was a project token (`lsv2_pt_...`) rather than a service key, that
it therefore could not reach the workspace owning `nexus-ai-assistant`, and that
the fix was to issue a new key. **All three claims were wrong.** The key was
never the problem, and a freshly minted `lsv2_sk_...` would have hit the exact
same 403.

The cause is that **LangSmith is four separate deployments**, each with its own
accounts, its own API host, and its own authentication:

| | API host | Sign-in UI |
|---|---|---|
| GCP US | `https://api.smith.langchain.com` *(SDK default)* | `https://smith.langchain.com` |
| GCP EU | `https://eu.api.smith.langchain.com` | `https://eu.smith.langchain.com` |
| GCP APAC | `https://apac.api.smith.langchain.com` | `https://apac.smith.langchain.com` |
| AWS US | `https://aws.api.smith.langchain.com` | `https://aws.smith.langchain.com` |

LangChain's docs state that resources live in the region "corresponding to the
URL where sign-up occurred" and that LangSmith authenticates via Supabase — so
those four sign-in pages share no accounts. This project's workspace
(`708479fc-5ba2-496b-b30f-f313bee4d409`, display name `Workspace 1`, created
2026-09-05) lives on **AWS US**. `backend/.env` set no `LANGSMITH_ENDPOINT`, so
the SDK used its GCP-US default and presented a key the AWS deployment had
never heard of.

Measured 2026-10-05 with this repo's own key, all four hosts in the same
minute:

| host | `GET /sessions?limit=1` | `GET /settings` |
|---|---|---|
| `api.smith.langchain.com` | 403 | 403 |
| `eu.api.smith.langchain.com` | 403 | 403 |
| `apac.api.smith.langchain.com` | 403 | 403 |
| `aws.api.smith.langchain.com` | **200 `[]`** | **200** |

Same key, same instant. Three hosts reject it; the fourth accepts it. That
single table is the whole diagnosis, and it is why the earlier "the key cannot
reach the workspace" reading was wrong: it had only ever been tested against the
one host that could not possibly know the key.

Two lessons this cost us:

- **A 403 does not mean "my key is bad."** It means "this host does not know
  this key" — which has at least two causes with completely different fixes
  (wrong region vs. genuinely insufficient scope). Probe every regional host
  before concluding anything about credential scope.
- **A 403 on ingest is invisible in the dashboard.** LangSmith logs the failure
  as a *warning* and the run completes normally, so an empty LangSmith is
  indistinguishable from an app that emitted no spans. That is why this file
  was written in the first place.

### Status: fixed and verified

`LANGSMITH_ENDPOINT=https://aws.api.smith.langchain.com` and
`LANGSMITH_TRACING=true` are now set in `backend/.env`. The endpoint line is
also documented in `backend/.env.example` and declared in
`backend/app/core/config.py`, and `backend/tests/test_langsmith_region.py`
guards both.

Verified end to end from this checkout on 2026-10-05, not merely configured:
the SDK resolved `aws.api.smith.langchain.com`, `/info` authenticated, an ingest
POST was **accepted**, it auto-created the `nexus-ai-assistant` project (which
did not exist before — that is normal, LangSmith creates it on first successful
ingest), and the run read straight back via `list_runs`. The read-only check
above cannot prove any of that; only the write can.

Two API facts that cost time and will do so again:

- `Client.create_run()` **returns `None`** in langsmith 0.12.1
  (`inspect.signature` confirms `-> None`). Reading `.id` off it raises
  `AttributeError`, which is indistinguishable from a rejected upload. Judge
  success by what the call *caused*, not by its return value.
- `Client` has **no `delete_run`** in 0.12.1, and `DELETE /runs/{id}` answers
  **405 Method Not Allowed**. Probe runs therefore have to be removed in the
  LangSmith UI.

What this file *is*: the span tree from the run captured on 2026-10-04, taken
from the `astream_events` payloads (each carries `run_id`, `parent_ids`,
`name`). Nothing here is simulated. The original run did not reach LangSmith —
it is kept as the baseline to compare a real uploaded trace against.

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

## To verify tracing is actually working

Check all three. A dashboard alone proves nothing — a silent 403 leaves an empty
LangSmith that is indistinguishable from an app emitting no spans.

**1. The key is accepted by the host you configured:**

```bash
# from backend/
uv run python -c "import os,urllib.request; u='https://'+os.environ['LANGSMITH_ENDPOINT'].split('://')[1]; urllib.request.urlopen(urllib.request.Request(u+'/sessions?limit=1',headers={'x-api-key':os.environ['LANGSMITH_API_KEY']}))"
```

`HTTPError 403` means the endpoint is wrong for this key — check the region
table above before concluding anything about the key itself.

**2. A run actually gets ingested** (this is the one the read-only check cannot
prove):

```bash
uv run python -c "from langsmith import Client; c=Client(); print(c._host); c.create_run(name='nexus-ingest-check', project_name='nexus-ai-assistant', run_type='llm', inputs={'probe':True}, outputs={'probe':True}); print('ingest OK')"
```

**3. The runs come back:**

```bash
uv run python -c "from langsmith import Client; print(len(list(Client().list_runs(project_name='nexus-ai-assistant', limit=5))))"
```

Note: the tracing project `nexus-ai-assistant` is created automatically by the
first successful ingest. Before that, `list_projects` legitimately returns empty
— that is not a failure.

### If you need a service key anyway

Worth doing eventually: a PAT (`lsv2_pt_`) carries its creator's personal
permissions and **stops working if that user leaves the organization**. An
application should use a service key (`lsv2_sk_`), which is workspace-scoped and
independent of any person. Settings → API Keys → **Service** tab → Create API
Key → workspace-scoped → expiration → Create. It is shown once. Wait ~60s
afterwards: authentication results are cached, so changes are not instant.

Pick **workspace-scoped**, not organization-scoped. An org-scoped service key
*requires* an `X-Tenant-Id` header for workspace-scoped resources, and omitting
it returns `403 Forbidden` — the same error, a completely different cause.
