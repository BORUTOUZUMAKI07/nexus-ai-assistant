> Part of the repo reference. Moved from `AGENTS.md` §1; the section
> numbering is preserved so existing `§1.N` references still resolve.
> Start at [`AGENTS.md`](../../AGENTS.md) for the session contract.

## 1. What this project is

Nexus AI Assistant is a full-stack **AI copilot** ("enterprise-grade AI
assistant"): a Python **FastAPI** backend implementing a **DDD modular
monolith** with a **LangGraph** multi-agent orchestrator, plus a **Next.js 16**
React frontend. It is not a toy demo — it has real auth (JWT + OAuth + 2FA +
email verification), a 35-table PostgreSQL schema, hybrid RAG on Qdrant, Celery
async jobs, mem0 memory, orgs/shares/webhooks, admin observability, and a CI
system that gates prompt regressions with live LLM judges.

**Monorepo.** Three top-level concerns:

| Path | Language/stack | Role |
|---|---|---|
| `backend/` | Python 3.11+ (CI: 3.12), FastAPI, SQLModel, LangGraph, Celery | The whole API + business logic. All Python code lives under `backend/app/`. |
| `frontend/` | TypeScript, Next.js 16.4.2 (App Router), React 19.2.8, Vitest, Playwright | Chat UI with BFF-style route handlers that proxy the backend. |
| `docs/`, `Makefile`, `docker-compose.yml`, `.github/` | Markdown, shell, YAML | Operations, infra, CI. |

The frontend talks to the backend through **Next.js route handlers**
(`frontend/src/app/api/**/route.ts`) that reverse-proxy to the FastAPI server;
the browser never talks to `localhost:8000` directly.
