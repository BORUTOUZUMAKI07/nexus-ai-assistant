# Tasks

## Phase 1: Strengthen recalled memories on every read path
- [x] Task 1: Reinforce recalled IDs in direct sync `/messages`
- [x] Task 2: Reinforce recalled IDs in direct stream `/messages`
- [x] Task 3: Reinforce recalled IDs in RAG/file search
- [x] Checkpoint: focused memory/API tests green (22 passed), ruff + typecheck green, committed as `0e4f319`

## Phase 2: Cache correctness
- [x] Task 4: Scope semantic cache to `conversation_id`
- [x] Task 5: Align cache invalidation key/variant with memory-scoped writes
- [x] Task 6: Give exact cache a memory-change invalidation hook
- [x] Checkpoint: cache tests green (27 + 24 + 19 focused tests); ruff + typecheck green; commits `aa4a58c`, `a03c482`, `4fcddeb`

## Phase 3: Data completeness and user control
- [x] Task 7: Include mem0-backed facts in GDPR export/erasure
- [x] Task 8: Make `enable_memory` actually gate backend memory behavior
- [x] Checkpoint: 155 account/settings/memory/agent tests green; ruff + typecheck green; commits `cad7519`, `1cec9e9`

## Phase 4: Consolidation and hygiene
- [x] Task 9: Route both prompt-memory appenders through the prompt compiler seam
- [x] Task 10: Align confidence defaults and resolve unused lifecycle helpers
- [x] Checkpoint: backend 1980 passed / 9 skipped / 12 deselected; frontend 591 passed; ruff + typecheck clean; guardrail diff check clean; commits `d91356e`, `e9fc0c5`, `dc403c6`

## Phase 5: Audit
- [x] Task 11: Deliver written audit of all features and services (see `docs/feature-service-audit.md`; plain-language summary delivered in chat)

## Standing guardrails
- Cron in `.github/workflows/nightly.yml` stays `0 6 * * *`.
- `Connection: keep-alive` stays in `backend/app/api/v1/messages.py` and `frontend/src/app/api/chat/route.ts:346`.
- No new test skips/suppressions; no secrets in files or commits.
- Every slice: RED test → GREEN fix → focused tests → lint/typecheck → commit.
