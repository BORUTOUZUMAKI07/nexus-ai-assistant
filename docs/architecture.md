# Nexus AI Assistant — System Architecture

## Overview
Nexus AI Assistant is an enterprise-grade AI copilot built on a **Domain-Driven Design (DDD) Modular Monolith** architecture with FastAPI and LangGraph on the backend, and a Next.js 14 glassmorphic interface on the frontend.

```mermaid
graph TD
    Client[Next.js 14 Frontend / FastMCP Client] -->|REST / SSE / MCP| API[FastAPI Gateway]
    API --> Auth[JWT & Rate Limiter / Upstash Redis]
    API --> Orchestrator[LangGraph Supervisor Graph]
    
    subgraph "Agents Layer"
        Orchestrator --> Planner[Planner Node]
        Orchestrator --> Router[Orchestrator Router]
        Orchestrator --> Researcher[Researcher Subagent]
        Orchestrator --> Coder[Coder Subagent]
        Orchestrator --> Critic[Critic Reflection Subagent]
        Orchestrator --> Synthesizer[Synthesizer Node]
        Orchestrator --> HITL[Human-in-the-Loop Interrupt]
    end

    subgraph "Services & Memory"
        Orchestrator --> Mem0[mem0 Long-Term Memory]
        Orchestrator --> RAG[Hybrid RAG Pipeline]
        Orchestrator --> Tools[E2B Sandbox / Firecrawl / Tools]
    end

    subgraph "Infrastructure & Storage"
        Mem0 --> Qdrant[(Qdrant Cloud Hybrid Vector DB)]
        RAG --> Qdrant
        API --> Postgres[(PostgreSQL / Supabase + SQLModel)]
        API --> Redis[(Upstash Redis Cache & Rate Limit)]
    end
```

## Core Modules & Boundaries

1. **Domain Layer (`backend/app/domain/`)**:
   - `user`: Identity, credentials, BYOK API key encryption (AES-256 Fernet), preferences.
   - `conversation`: Thread persistence, tree branching, multi-turn messages, attachments.
   - `file`: Document indexing metadata, chunk registries, vector IDs.
   - `tool`: Available tools registry, granular user permissions, audit execution logs.
   - `prompt`: Template catalog, version trees, system skills.
   - `usage`: Per-turn token usage, monthly cost tracking, DeepEval/Ragas score logs.
   - `system`: Dynamic system configuration, RFC-7807 exceptions, compliance audit trail.

2. **Agents Layer (`backend/app/agents/`)**:
   - `orchestrator`: Compiled StateGraph with `AsyncPostgresSaver` checkpointing, dynamic tool dispatching, and LangGraph HITL approval gates.
   - `subagents`: Specialized execution roles for web research (`researcher.py`), sandboxed python code execution (`coder.py`), and self-critique (`critic.py`).

3. **Hybrid RAG Pipeline (`backend/app/services/rag/`)**:
   - Ingests Markdown, PDF, Code, JSON.
   - Semantic sliding window chunking with header hierarchy preservation.
   - Dual-vector generation (dense BAAI/bge-small-en-v1.5 + sparse BM25).
   - Multi-stage retrieval with Reciprocal Rank Fusion (Fusion.RRF) and FlashRank cross-encoder reranking.
   - Grounded claim verification and citation attribution.
