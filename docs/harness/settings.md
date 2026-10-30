> Part of the repo reference. Moved from `AGENTS.md` §5; the section
> numbering is preserved so existing `§5.N` references still resolve.
> Start at [`AGENTS.md`](../../AGENTS.md) for the session contract.

## 5. Key settings & env (from `backend/app/core/config.py` + `.env.example`)

Sensible defaults exist for everything; `.env` overrides. Highlights:

- `API_V1_PREFIX=/api/v1` — used for all route mounts (single source; one
  setting, not the old duplicated `API_V1_STR`).
- `MCP_AUTH_ENABLED=True` (gate FastMCP), `MCP_API_KEY` (shared key auth).
- `RESPONSE_CACHE_ENABLED=false` + `RESPONSE_CACHE_TTL_SECONDS=3600`
  (semantic-cost cache).
- `EXPERIMENTS_CONFIG_PATH=` → canary/shadow experiments YAML (unset =
  default variant).
- `SHARE_DEFAULT_TTL_SECONDS` — default public-share expiry.
- **Researcher (D1)** — `RESEARCH_MAX_SUBQUERIES=3`, `RESEARCH_MAX_SCRAPES=4`,
  `RESEARCH_SCRAPE_CONCURRENCY=3`, `RESEARCH_MAX_REFLECTIONS=1`,
  `RESEARCH_EVIDENCE_FLOOR_CHARS=1200`, `RESEARCH_SYNTHESIS_MAX_TOKENS=1400`,
  `RESEARCH_MAX_MODEL_CALLS=4`. The scrape budget is a **total across all
  sub-queries**, not per sub-query, or breadth multiplies into unbounded spend.
  Decomposition and reflection are each skipped when they cannot pay, so the
  common case costs what it cost before.
- **Run spend ceiling (D2)** — `AGENT_LOOP_MAX_STEPS=8`,
  `AGENT_LOOP_TOKEN_BUDGET=60000`. Every loop in the graph is individually
  bounded and their product is not; this is the only cumulative check.
- **Artifact generation (D3)** — `ARTIFACT_GENERATION_ENABLED=true` (master
  switch; false makes the decision record-only, still logged, nothing written),
  `ARTIFACT_MIN_DOCUMENT_CHARS=1200`, `ARTIFACT_MIN_CODE_CHARS=400`,
  `ARTIFACT_MIN_CODE_SHARE=0.25`, `ARTIFACT_MIN_CLASSIFIER_CHARS=200`,
  `ARTIFACT_ALLOW_CLASSIFIER=true`. The two length floors are **separate on
  purpose**: a prose document is long *because* it is a document, and a code
  file is worth keeping well before it reaches prose length.
  `ARTIFACT_MIN_CODE_SHARE` is the main false-positive guard — without it,
  `"use % to test evenness: ```x % 2 == 0```"` becomes a source file, because
  a closed fence cannot tell a file from an illustration. Fence *count* is the
  wrong discriminator; *proportion of the answer* is the right one. Turning
  `ARTIFACT_ALLOW_CLASSIFIER` off leaves the free signals deciding alone — that
  is how the thresholds were tuned, and it is the setting to reach for if LLM
  spend matters more than recall.
- **HyDE (retrieval)** — `RAG_HYDE_MODE=llm|template|off` (default `llm`),
  `RAG_HYDE_MODEL=fast_chat`, `RAG_HYDE_MAX_TOKENS=220`,
  `RAG_HYDE_TIMEOUT_SECONDS=6.0`. The model call is per *abstract* query only —
  `should_generate_hyde` skips code, path, and keyword lookups before any spend.
  `template`/`off` exist so the cost/benefit is measurable by flipping one
  variable; see §9.19 for why the template is a fallback rather than the
  implementation.
- **Typed decisions / answer coverage** — `DECISION_MODEL=fast_chat`,
  `DECISION_MAX_TOKENS=8` (a hard ceiling: the distribution is read at the first
  token), `DECISION_TIMEOUT_SECONDS=6.0`, `DECISION_TOP_LOGPROBS=20`;
  `RAG_ANSWER_COVERAGE_ENABLED=false` (off by default — it is genuine new cost on
  the query path), `RAG_ANSWER_COVERAGE_TOP_N=5`,
  `RAG_ANSWER_COVERAGE_DROP_BELOW=0.6`, `RAG_ANSWER_COVERAGE_CHARS=1200`.
  See §9.20 for why a probability here is `None` unless it was measured, and
  §9.21 for why coverage is a drop-only-on-measurement stage with an
  empty-evidence guard.
- `JWT_ALGORITHM=HS256`, refresh tokens, `oauth2_scheme` tokenUrl=auth/login.
- `REDIS_URL=redis://localhost:6379/0` (local Redis from docker-compose).
- `NEW_RELIC_ENABLED` + `NEW_RELIC_OTLP_ENDPOINT` (optional OTLP
  log export to the Layer-2 collector — see `docker/otel-collector-config.yaml`),
  `MEM0_API_KEY`.
- The New Relic ingest key lives on the **collector**, never the app
  (`NEW_RELIC_LICENSE_KEY` in docker-compose/render.yaml; the app holds no
  vendor credentials). `/metrics` stays the in-process source of truth
  (scraped by the collector); traces are LangSmith's job; the old
  `services/observability/newrelic.py` bridge was retired to avoid
  double-counting metrics (app scrape + OTLP push).
- Bringing New Relic live is a **pending ops task** — see
  `docs/observability-deployment.md` (collector deploy → secrets → app env →
  optional `METRICS_TOKEN` guard). Code is done and on `main`; do not re-do it.
- Model routing: LiteLLM router (see `backend/app/services/llm/` + a routing
  config file); the live-evаl judge also uses LiteLLM (needs
  `OPENROUTER_API_KEY`).

Verified `.env.example` has mojibake only in the PowerShell console **render**
— the files themselves are proper UTF-8 (emoji/box-drawing chars). Do not
"fix" them.
