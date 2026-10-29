> Part of the repo reference. Moved from `AGENTS.md` §2; the section
> numbering is preserved so existing `§2.N` references still resolve.
> Start at [`AGENTS.md`](../../AGENTS.md) for the session contract.

## 2. Verified load-bearing facts (do not "fix" these)

These were each confirmed first-hand and are intentional:

- **Windows uvicorn** must run with
  `--loop backend.app.infrastructure.common.event_loop:event_loop_factory`
  (`ProactorEventLoop` breaks Redis/asyncpg). The factory is in
  `backend/app/infrastructure/common/event_loop.py`; `backend/app/main.py`
  documents the exact flag.
- **MCP mount**: FastMCP is mounted at `/mcp` with `MCP_AUTH_ENABLED=True`
  (verified: log line `fastmcp_mounted ... auth_enabled=True path=/mcp`).
  Requests are gated by a middleware accepting: Bearer JWT, the
  `nexus_access_token` cookie, or the `x-nexus-mcp-key` shared key.
- **JWT is HS256 only, on PyJWT — not python-jose.** The app signs HS256 only
  (`JWT_ALGORITHM` in `core/config.py`), and since the Strix remediation
  (§9.50) the library is **PyJWT**: `core/security.py` does `import jwt` +
  `from jwt import PyJWTError as JWTError` (the alias keeps every
  `except JWTError` and every call site unchanged), and `pyproject.toml`
  declares `pyjwt>=2.10.0`. **`python-jose` and the `ecdsa` it dragged in are
  gone from the tree** — python-jose 3.5.0 is the terminal release, so
  PYSEC-2026-4298 (algorithm confusion) has no 3.x fix, and PYSEC-2026-1325
  (Minerva) has no patched release either. CI's `pip-audit` therefore runs with
  **no `--ignore-vuln` flags at all**. Do **not** reintroduce `python-jose`, and
  do **not** add ES* algorithms without re-reviewing ecdsa.
- **OAuth is provider-based, GET-login / POST-callback**: two first-class OAuth
  providers — Google (OIDC; the userinfo `email_verified` claim gates the
  login) and GitHub (OAuth2; a verified email — primary preferred — is
  re-fetched from `/user/emails` because GitHub has no trustable userinfo
  claim).
  `GET /auth/oauth/{provider}` returns the provider authorization URL (PKCE)
  and `POST /auth/oauth/{provider}/callback` accepts the code the provider
  hands back — both 404 while that provider (`google` | `github`) is
  unconfigured or unknown. Verified in `api/v1/auth.py` +
  `services/oauth_service.py` (`OAuthProviderRegistry` with
  `GoogleOAuthProvider`/`GitHubOAuthProvider`); there is **no** flat
  `/auth/oauth/login` route.
- **`GET /auth/oauth/providers` is declared BEFORE `/oauth/{provider}`, and it
  has to be.** It reports which IdPs this deployment can actually complete a
  login with (`OAuthProviderRegistry.describe_providers()`), so the sign-in page
  can render exactly those buttons instead of a hardcoded pair. FastAPI matches
  in registration order, so a literal path registered *after* the catch-all is
  captured as a provider named `providers` and answered with the "unconfigured"
  404 — indistinguishable from OAuth being off, with no test anywhere red. The
  same ordering rule applies twice more on the way out: Next's
  `app/api/auth/oauth/providers/route.ts` sits beside `[provider]/route.ts`, and
  the MSW mock at `src/test/mocks/handlers.ts` sits above the `:provider`
  handler. `:provider` matches the string `"providers"`, so all three would be
  shadowed by the wrong order. The endpoint is **unauthenticated** by
  necessity — the sign-in page has no session yet, and that is the only moment
  it needs this — and returns names plus a boolean, nothing else.
- **"SSO" was renamed away; the codebase says "OAuth" throughout.** Single
  sign-on is the *outcome* ("you were already signed in to Google, so you are
  signed in here"); OAuth 2.0 / OIDC is the *protocol* that produces it. Google
  and GitHub OAuth **are** SSO, and the sibling projects call the mechanism
  OAuth, so `SSOProviderRegistry` → `OAuthProviderRegistry`,
  `AuthService.sso_login` → `oauth_login`, and the frontend
  `SsoAuthorizationUrl`/`SsoProviderInfo`/`listSsoProviders`/`ssoLogin`/
  `SSO_LABELS`/`SSO_GLYPHS` became `OAuthAuthorizationUrl`/`OAuthProviderInfo`/
  `listOAuthProviders`/`startOAuthLogin`/`PROVIDER_LABELS`/`PROVIDER_GLYPHS`
  (`services/oauth_service.py`, `frontend/src/lib/api.ts`,
  `frontend/src/app/signin/page.tsx`). It remains **one** auth path — there is
  no separate SSO mechanism, no SAML, no third provider; the rename is
  terminology only. **Do not introduce a second auth path under the OAuth
  name.**
- **8 `except` handlers in `backend/app/` have `pass` as their entire body,
  and are deliberate** — each is a fail-open or fail-silent seam (e.g.,
  telemetry, non-critical caches). Don't blanket-remove them. A ~64 more are
  "silent" only in the sense that their body is a docstring plus a `return` /
  `assign`; those are real logic, not swallows. **Prefer a documented body
  over a bare `pass` in new code** — the new Batch C/D modules deliberately use
  a docstring-then-`return` form so the intent is legible and `bandit` (a CI
  gate) stays at zero. Verify any count you rely on with
  `ast.walk(tree)`, not a regex: on CRLF checkouts a `\s*\n\s*` pattern
  matches nothing and you will conclude the seams were removed.
- **No mutable default arguments** anywhere in `backend/app/` (verified).
- **mypy is NOT a gate on the full tree.** It only runs over the 30-file
  allowlist in `backend/scripts/mypy_targets.txt`, and the command needs those
  paths passed explicitly (`--follow-imports=silent`, `backend/` prefix
  stripped) — bare `uv run mypy` has no target and exits 2. The full app has
  ~466 pre-existing strict-mode errors (75 files). Do not attempt a full fix.
  New modules belong on the allowlist: check one with
  `uv run mypy --follow-imports=silent <module>` and append it if clean.
  **Fix the errors rather than allowlisting the module away** — §9.27 is the
  case where the only self-contradicting annotation in five new modules
  (`frames.extend(list[str])` into a `list[dict]`) turned out to be a real
  defect that had silently deleted three SSE frames, and no test found it.
  Two diagnostics *cannot* be fixed without abandoning the repo idiom and carry
  targeted `type: ignore[attr-defined]` comments instead: `AgentRun.created_at.desc()`
  and `RunEvent.seq.asc()`.
- **`ResilientPostgresSaver` must subclass `BaseCheckpointSaver`, and did
  not.** `StateGraph.compile()` calls `ensure_valid_checkpointer`, which is an
  `isinstance` check
  (`.venv/.../langgraph/types.py:109`); `lifespan_graph` catches the resulting
  `TypeError` and falls back to `InMemorySaver`. So the wrapper shipped as a
  duck-typed class with every right method, was *discarded at compile time*, and
  every one of its 18 tests passed — they all exercise the wrapper directly and
  never ask LangGraph whether it accepts it. Production ran with no
  persistence while the reconnect logic never executed once. The class now
  subclasses the base and `FORWARDED_METHODS` is the base's full async surface
  (anything not forwarded inherits a `NotImplementedError` stub).
- **Run `pytest` from `backend/`, never the repo root.** The Batch A–D test
  files open source files by repo-root-relative path (`app/services/...`), so
  from the repo root every one of them raises `FileNotFoundError`: 131 spurious
  failures and 124 errors that look like a catastrophic regression and are
  entirely a CWD mistake. `uv run --project backend pytest` from the root hits
  this; `cd backend && uv run pytest` is the documented run.
- **No tracked `.env`.** Only `.env.example`. Real secrets live in CI
  variables / GitHub Actions secrets. Pushed history contains no real secrets
  (verified).
- **`--code-block-bg` in `src/lib/theme.ts` is intentional** (a deliberate
  comment explains it) — do not "clean up" its duplicate declaration.
- **`docs/alerting/alert-rules.yml` is the single source of truth** for
  paging. SLOs in `docs/slo.md` and runbooks in `docs/runbooks/` match it —
  each alert links to its runbook. Keep all three in sync.
- **"Books" were removed deliberately.** `AI_Engineering_Complete_Notes.md`
  and `ml_systems_design_master_notes.md` (and the audit docs that cited them)
  were deleted; **do not recreate them**. Code comments that once cited
  "§x.y of AI_Engineering_Complete_Notes" have been rewritten to describe the
  pattern inline.
- **The injection guardrail canonicalizes before it matches, and the fold is
  lossy on purpose.** `guardrail_service.canonicalize_for_detection()` folds
  NFKC, invisible characters, Cyrillic/Greek homoglyphs and per-token leetspeak,
  because the raw-ASCII regexes were measured to miss 5 of 9 substitution
  variants. It is used **only** to build a string to match against — never to
  rewrite stored or user-visible text. That boundary is load-bearing: the fold
  mangles Russian prose into word soup, which is fine in a discarded copy and
  would be a serious bug on a persistence path.
  The guardrails we already had, in case they look absent: PII redaction +
  injection detection (`guardrail_service.py`), the evidence gate
  (`evidence_gate.py`), a permission ladder with approval-required tools
  (`tool_gateway.py` + `config/permissions.yaml`), untrusted-data wrapping of all
  RAG/web/tool output (`nodes.py::_wrap_untrusted`), SSRF blocking, and
  role-gated admin routes.
  **`r"jailbreak"` is not a trigger on its own** — it blocked anyone naming the
  topic, including security engineers asking how to defend against it, and a
  guardrail like that gets switched off. It matches the actor position
  ("enable jailbreak", "jailbreak mode", "you are now jailbroken"). No profanity
  filter exists and one is deliberately not wanted; a keyword screen cannot
  catch novel slurs while producing the false positives that get it disabled.
- **`backend/config/` contains exactly one file, `permissions.yaml`, and it is
  the only one with a reader** (`tool_gateway.py:50`). Three dead files were
  deleted 2026-10-01 and must not come back:
  - `model_routing.yaml` — `Router(...)` in `litellm_client.py` is built from
    Python literals with no `config_path`; the yaml had drifted (fast_chat
    llama-3.1-8b-instant vs the code's qwen3.8-27b, an `audio_transcription`
    group that does not exist, cooldown 60 vs 30).
  - `task_contracts.yaml` — describes critic contracts (`constraints` /
    `done_when` / `escalate_when`) and three context-budget tiers (0.60/0.85/
    0.95). `critic.py:14-25` checks a different four-part rubric; `context_
    compiler.py:41` has a single ratio, 0.75. Six numbers, zero live.
  - `eval_criteria.yaml` — G-Eval rubrics for a third-party evaluation library
    that was removed from dependencies over CVEs, plus a profanity sanitize tier
    that no code implements.
  `tests/test_no_dead_router_config.py` guards the router case only — it
  enumerates **git-tracked** YAML, not `rglob`, because this checkout holds an
  untracked `.kilo/worktrees/debonair-redcurrant/` worktree with a full copy of
  the project and a filesystem scan fails on that copy.
