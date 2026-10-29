# Golden Paths — the journeys that must actually work

A golden path is a user journey with a **machine-checkable** definition of clean.
"Verify all the features" is not a test; this table is. Every row names the
observable end state and the spec that proves it, so a browser check has a pass
condition instead of an opinion.

**Why this file exists.** Unit tests are structurally blind to component
boundaries — the mock that makes a test fast is the same mock that hides a wrong
path between the BFF route and the FastAPI endpoint. A journey exercised end to
end in a real browser is the only evidence that the wiring is right. See
`docs/architecture.md` for the layering this crosses.

Owned by: anyone changing a route, a BFF handler, or a view. Update it in the
same change, not later.

---

## How to run the automation

```bash
make e2e                      # all 25 specs, Playwright, baseURL http://localhost:3000
cd frontend && npx playwright test e2e/files.spec.ts   # one journey
cd frontend && npx playwright test --headed            # watch it
```

`webServer` in `frontend/playwright.config.ts` starts the Next dev server; the
**backend must already be running** on :8000, and Docker (Postgres/Qdrant/Redis)
must be up for the knowledge and chat journeys. A red run with a connection
refused is an environment failure, not a product one — check `./init.sh` first.

## Manual validation loop (when no spec covers the path yet)

Repeatable loop, in order. One path at a time; never several at once, or a
failure cannot be attributed.

1. Clear stale console noise (`preview_logs` / DevTools console).
2. Capture the BEFORE state (screenshot or snapshot of the DOM).
3. Trigger exactly one path.
4. Observe runtime events: network status, console errors, visible text.
5. Capture the AFTER state.
6. If it failed, fix the smallest responsible layer and restart the app.
7. Re-run the *same* path and compare BEFORE/AFTER.

**Clean means:** intended visible state present · no unexpected error toast ·
console has no unexplained error · the request returned 2xx · re-running the same
path gives the same result.

---

## Auth

| Journey | Observable clean state | Spec |
|---|---|---|
| Anonymous visitor hits `/app` | Redirected to the sign-in gate; no conversation list rendered | `auth.spec.ts` "anonymous visitor sees the login gate" |
| Sign in with valid credentials | Lands in `/app`, conversation list loads, no error toast | `auth.spec.ts` "valid credentials enter the app and load conversations" |
| Sign in with a bad password | Readable error text (not a stack trace, not a silent no-op) | `auth.spec.ts` "invalid credentials show a readable error" |
| Sign out | Session cookie cleared, back at the landing page, `/app` protected again | `auth.spec.ts` "signing out clears the session and returns to the landing page" |
| Already-authenticated visitor opens `/signin` | Redirected into `/app` instead of seeing the form | `auth.spec.ts` "sign-in page redirects to the app when already authenticated" |

## Chat — the core journey

| Journey | Observable clean state | Spec |
|---|---|---|
| Send a message | User bubble appears immediately; assistant reply streams in and persists after reload | `chat.spec.ts` "sends a message and streams an assistant reply" |
| Web toggle | Composer shows Web active; the run records `mode: research`; the answer cites a URL | `chat.spec.ts` "web toggle sends the chat in research mode" |
| New conversation | Sidebar gains a row whose id is the backend id (not a client-side uuid) | `conversations.spec.ts` "starts a new conversation with the backend id" |
| Delete a conversation | Row disappears, the other conversation survives, no 500 | `conversations.spec.ts` "deletes a conversation and keeps the remaining one" |
| Knowledge tab | Indexed documents listed with status; empty state is explicit | `chat.spec.ts` "knowledge tab lists indexed documents" |
| Settings tab | BYOK providers and persistent memories render from the backend | `chat.spec.ts` "settings tab shows BYOK providers and persistent memories" |

## Files and retrieval

| Journey | Observable clean state | Spec |
|---|---|---|
| Upload a document | Appears in the index list; no silent failure | `files.spec.ts` "uploads a document into the index" |
| List indexed documents | Rows match the backend, not a stale cache | `files.spec.ts` "lists indexed documents" |
| Semantic search | Query returns ranked chunks from the RAG testbed | `files.spec.ts` "runs a semantic search against the RAG testbed" |
| Delete a document | Row gone and the chunk count drops | `files.spec.ts` "deletes a document from the index" |
| Index backend down | A readable error, not an infinite spinner | `files.spec.ts` "shows a readable error when the index backend is unavailable" |

## Usage

| Journey | Observable clean state | Spec |
|---|---|---|
| Open the usage view | Metric cards populated from `/usage`, not hardcoded | `usage.spec.ts` "shows backend-driven usage metric cards" |
| Token breakdown | Input/output split rendered and summing correctly | `usage.spec.ts` "renders the input / output token breakdown" |
| Real stats | Values match `/usage` for the signed-in user | `usage.spec.ts` "reflects real backend stats when available" |

## Admin

| Journey | Observable clean state | Spec |
|---|---|---|
| User directory | Rows with role and status for a real admin | `admin.spec.ts` "lists the user directory with roles and status" |
| Disable / re-enable a user | Status flips and survives a reload | `admin.spec.ts` "disables and re-enables a user" |
| System health | Health cards reflect backend-not-ok rather than always-green | `admin.spec.ts` "shows system health cards" |
| Audit logs | Compliance entries render with actor and action | `admin.spec.ts` "renders compliance audit logs" |

---

## Journeys with **no** automated spec yet

These are real, shipped surfaces that only a manual pass can currently prove.
A journey moves out of this list by gaining a spec — that is the whole
"review feedback promotion" loop: a recurring manual check becomes an
automated one.

| Journey | What to check by hand |
|---|---|
| Edit a message and re-run from that point | Edited text replaces the old text in the transcript **and** is what the model sees on the next turn |
| Regenerate the last answer | New answer replaces the old one; no duplicate message row |
| Artifact canvas | A document-shaped answer opens the canvas; an edit saves a version; regeneration keeps the same title |
| Citation inspector | Clicking a citation opens the inspector with the right chunk |
| Voice input / TTS | Dictated text lands in the composer; the reply can be read back |
| Organizations + webhooks (`IntegrationsSettings`) | Invite a member, accept it, register a webhook endpoint, see a delivery |
| Public share link | Generated URL uses `APP_PUBLIC_URL`, opens `/share/{token}` signed out, and renders the conversation |
| Command palette (`⌘K` / `Ctrl+K`) | Opens, filters, and navigates |
| Plan mode | A plan is produced and reappears in plan history |

## Adding a journey

1. Write the row with an observable clean state — text that is present, a button
   enabled, an error gone, a 2xx request. Not "works correctly".
2. Add the Playwright spec (or note it as manual).
3. Run it once before the change and once after; a spec that passes both times
   proves nothing about the change.
