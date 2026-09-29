# Nexus AI Assistant — User Guide

> Written against the frontend at `1f43c89` (Next.js 16.3.4). The app
> combines a streaming chat with plan mode, RAG knowledge base, artifacts,
> settings, usage, and admin views.

## 1. Getting started

1. Launch the stack (see [`deployment-guide.md`](deployment-guide.md)):
   - Infra: `docker compose up -d`
   - Backend: `cd backend && uv sync --group dev && uv run alembic upgrade head && uv run uvicorn backend.app.main:app --reload --port 8000`
   - Frontend: `cd frontend && npm install && npm run dev`
2. Open `http://localhost:3000`.
3. Create an account (**Sign Up**) or sign in (**Sign In**). SSO (OAuth) is
   also available from the sign-in screen; email verification and optional
   2FA (TOTP) protect the account.

## 2. Chat & streaming

- Responses stream in real time over **Server-Sent Events (SSE)** with
  thinking/CoT blocks, numbered citations, and tool-call cards inline.
- Type `Enter` to send; `Shift+Enter` for a newline.
- **Slash commands** open from `/` in the input:

  | Command | Effect |
  |---|---|
  | `/code` | Activate Python sandbox & code agent (E2B) |
  | `/web` | Enable live web search (Firecrawl/DDG) |
  | `/deep` | Deep agent — LangGraph tree-of-thought reasoning |
  | `/doc` | Open the document picker (attach a file) |
  | `/review` | Senior-level code review |
  | `/research` | Structured multi-perspective research brief |

  Navigate with `↑`/`↓`, apply with `Tab`/`Enter`, dismiss with `Esc`.

## 3. Plan mode (plan-then-approve)

- Generate a plan for a task (`POST /api/v1/conversations/{id}/plan`) — the
  agent proposes ordered steps instead of executing immediately.
- Review the plan card in the chat feed, then **Approve** or **Reject**.
  Approving pins the approved steps as a system-prompt preamble so the agent
  follows them without silently deviating.
- Full plan history for a conversation is listed in the **Plan History** panel.

## 4. Knowledge base & document RAG

- Open the **Knowledge** tab.
- Upload PDF, Markdown, text, or JSON files (drag & drop or picker).
- Nexus extracts text, chunks it, embeds **dense + sparse** vectors, and
  indexes them into Qdrant — synchronously, or asynchronously via Celery
  (202 + `Location` header when `ASYNC_INDEXING=true`).
- Ask questions in chat about uploaded docs; answers include numbered
  citations linking back to the source chunks (`CitationInspector`).
- `POST /api/v1/files/rag/query` is also available programmatically.

## 5. Human-in-the-loop tool approvals

- Sensitive tools (destructive operations, shell commands, approvals) trigger
  an interactive approval card in the chat feed.
- Click **Approve** to execute or **Deny** to cancel and ask the agent for a
  different approach. Approvals resolve via
  `POST /api/v1/conversations/{id}/hitl`.

## 6. Artifacts

- The agent can produce **server-side artifacts** (versioned documents) —
  view/edit them in the artifact canvas from the sidebar.
- `⌘F`/`Ctrl+F` finds within an artifact, `⌘S`/`Ctrl+S` saves an edit, `Esc`
  closes the search / abandons an edit. Every edit creates a new version.

## 7. BYOK keys & usage monitoring

- **Settings** tab: add your own Groq, OpenRouter, or OpenAI keys
  (`/settings/keys`, encrypted at rest with AES-256 Fernet), manage mem0
  memories (`/settings/memories`), and adjust appearance.
- **Usage** tab: token consumption, per-model costs, evaluation history, and
  quota tracking (`/usage/summary`, `/usage/evaluations`).

## 8. Organizations, sharing & webhooks

- **Organizations** let teams share conversations; create orgs, invite
  members, and view per-org usage summaries.
- **Public shares** give time-limited (TTL) read access to a conversation via
  a link (`/shares/{conversation_id}`).
- **Webhooks** (integration/admin) notify external systems of events, with
  retryable deliveries and per-endpoint redelivery.

## 9. Admin (admins only)

- Manage users, lifecycle hooks, prompt templates, and system status.
- Run evaluations (arena, conversational, quality, redteam, prompt
  regression, RAG) and trigger prompt-optimization loops.
- Monitoring surfaces: drift report, popularity-bucketed slices, fairness,
  bandits, observability viewer.
- Audit reports (including EU-AI-Act and red-team summaries) under the
  **Audit** section.

## 10. Keyboard shortcuts

See [`keyboard-shortcuts.md`](keyboard-shortcuts.md) for the exact, verified
list (`⌘K` command palette, artifact `⌘F`/`⌘S`/`Esc`, chat `Enter`/`Shift+Enter`,
slash-menu navigation).

## 11. Mobile/desktop tips

- The sidebar collapses on narrow viewports; the command palette (`⌘K`) works
  from anywhere.
- Streaming is resilient: network blips surface an error state with retry
  (`error.tsx` / `AppErrorState`), and the tab re-syncs state on focus.