# Audit Finding Fixes — Implementation Slices

Goal: fix every finding in `docs/feature-service-audit.md`, then merge to local `main` and push (user approved on 2026-11-21).

Rules: RED test first, minimal GREEN, focused tests + ruff + typecheck, small commit. No new skips/suppressions/secrets. Keep cron `0 6 * * *`, both `Connection: keep-alive` headers, fail-open memory and per-user scoping.

| Slice | Finding | Status |
|---|---|---|
| A | M2 outbox constant backoff | done (`461e926`) |
| B | M1 secret-scan whole-file skip | done (`a5089a8`) |
| C | M6 + L5 doc/version drift | done (`ddc1c29`) |
| D | M3 observability status from real probe | done (`9c07b48`) |
| E | M4 image config is dead | done |
| F | H1 + H2 admin trace auth + real store | done |
| G | H3 backend 2FA login 500 / cookie gap | done |
| H | H3 frontend 2FA BFF + sign-in/enrollment UI | done |
| I | L3 admin evaluation UI | done |
| J | L4 backend-to-BFF route coverage test | done |
| K | L1 redundant webhook pre-check | done |
| L | L2 collector auth enforcement test | done |
| M | M7 optimistic proxy honesty | pending |
| N | M5 sync/stream chat parity | pending |
| Final | full verification, merge to main, push | pending |
