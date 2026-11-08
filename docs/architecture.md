# Architecture

## System Overview

Nexus AI Assistant is a distributed application with a Next.js frontend and a FastAPI backend orchestrating multi-agent workflows.

## High-Level Flow

```mermaid
flowchart LR
  U[User] --> F[Next.js Frontend]
  F --> BFF[Next.js API Routes BFF]
  BFF --> API[FastAPI Backend]
  API --> ORCH[LangGraph Orchestrator]
  ORCH --> AGENTS[Specialized Agents]
  ORCH --> TOOLS[Tools and MCP]
  ORCH --> LLM[LiteLLM Router]
  API --> DB[(PostgreSQL)]
  API --> REDIS[(Redis)]
  API --> VEC[(Vector Store)]
  API --> OBJ[Object Storage]
```

## Frontend
- App Router with React 19
- BFF pattern for authenticated requests
- TanStack Query for server state
- SSE streaming for chat

## Backend
- FastAPI with domain-driven structure
- LangGraph orchestration
- JWT authentication with OAuth
- Redis-backed rate limiting

## Agent Orchestration

```mermaid
flowchart TD
  START[User Message] --> ROUTER[Router Agent]
  ROUTER --> PLANNER[Planner]
  PLANNER --> EXECUTOR[Executor]
  EXECUTOR --> TOOLS[Tool Execution]
  TOOLS --> SYNTH[Synthesizer]
  SYNTH --> END[Response]
```

## Data Layer
- PostgreSQL with pgvector
- Redis for caching and rate limiting
- Qdrant for vector search
- Object storage for files

## Observability
- OpenTelemetry tracing and metrics
- Structured logging
- Health and readiness checks
