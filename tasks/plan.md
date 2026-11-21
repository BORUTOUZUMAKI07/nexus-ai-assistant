# Implementation Plan: Close Memory Design Gaps + Full Feature/Service Audit

## Overview
Fix all eight "unfinished edge" items identified in the memory design review, one TDD slice at a time, then deliver a factual audit of every other feature and service. Keep the app deployable after each slice: RED test, minimal GREEN fix, lint/typecheck/focused tests, commit.

## Architecture Decisions
- Preserve fail-open memory behavior: memory problems must never break a chat response.
- Preserve existing safe behavior by default; no new feature flag that silently disables memory.
- Cache keys must be derived by one canonical helper so writes, reads, and invalidations agree on conversation scope and memory variant.
- All prompt memory text goes through one canonical compiler seam.
- The broad audit is read-only reporting; do not silently change unrelated features.
- No test suppression, `@ts-ignore`, `eslint-disable`, skipped/deleted assertions, or secret values in files.

## Task List

### Phase 1: Strengthen recalled memories on every read path
- [x] Task 1: Reinforce recalled IDs in direct sync `/messages`
- [x] Task 2: Reinforce recalled IDs in direct stream `/messages`
- [x] Task 3: Reinforce recalled IDs in RAG/file search

**Checkpoint:** 22 focused tests passed; ruff and typecheck green; committed as `0e4f319`.

### Phase 2: Cache correctness
- [x] Task 4: Scope semantic cache to `conversation_id`
- [x] Task 5: Align cache invalidation key/variant with memory-scoped writes
- [x] Task 6: Give exact cache a memory-change invalidation hook

**Checkpoint:** focused cache/outbox/message suites green; ruff and typecheck green; commits `aa4a58c`, `a03c482`, `4fcddeb`.

### Phase 3: Data completeness and user control
- [x] Task 7: Include mem0-backed facts in GDPR export/erasure
- [x] Task 8: Make `enable_memory` actually gate backend memory behavior

**Checkpoint:** 155 account/settings/memory/agent tests green; ruff and typecheck green; commits `cad7519`, `1cec9e9`.

### Phase 4: Consolidation and hygiene
- [x] Task 9: Route both prompt-memory appenders through the prompt compiler seam
- [x] Task 10: Align confidence defaults and resolve unused lifecycle helpers

**Checkpoint:** 109 prompt/memory/agent focused tests and 101 confidence/lifecycle focused tests green; ruff and typecheck green; commits `d91356e`, `e9fc0c5`, `dc403c6`.

### Phase 5: Audit
- [x] Task 11: Deliver written audit of all features and services, separating live-verified from config-only

**Final verification:** backend 1980 passed / 9 skipped / 12 deselected; frontend 591 passed; ruff and typecheck clean; cron `0 6 * * *` and both `Connection: keep-alive` headers intact; zero new suppressions or secret patterns in the diff.

## Risks and Mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| Reinforcement called too often (every read) inflates confidence | Medium | Reuse the existing gate/limiter from the orchestrator path; add a focused test for repeated reads. |
| Cache key change invalidates existing entries | Low | Acceptable: stale entries are the bug; keys are ephemeral. |
| `enable_memory=false` breaks existing users | High | Default remains enabled; only explicit false disables; test both values. |
| GDPR export change exposes another tenant's data | High | Scope all mem0 list calls by authenticated `user_id`; add a negative test. |
| Removing "unused" helpers breaks hidden callers | Medium | Grep entire repo including tests before removal; prefer wiring when a real seam exists. |

## Open Questions
- Resolved: `decayed_confidence`, `apply_reinforcement` and the decay job were already wired; `partition_new_memories` was the one helper bypassing `is_duplicate`, so it is now routed through the shared policy instead of being deleted.
- Still open: whether to push `fix/memory-design-gaps` to `main` (prior work was pushed; confirm before pushing).
