# Nexus AI Assistant — API & FastMCP Reference

## Base URL
- Production: `https://<your-backend-domain>/api/v1`
- Local Development: `http://localhost:8000/api/v1`
- Swagger UI Documentation: `http://localhost:8000/docs`
- FastMCP SSE Endpoint: `http://localhost:8000/mcp`

---

## Authentication Endpoints (`/auth`)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/auth/register` | Register new account |
| `POST` | `/auth/login` | Authenticate with credentials and receive JWT |
| `POST` | `/auth/refresh` | Refresh expired access token using refresh token |
| `GET` | `/auth/me` | Retrieve authenticated user profile |

---

## Conversation & Streaming Endpoints (`/conversations`)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/conversations` | List user's conversations |
| `POST` | `/conversations` | Create a new conversation |
| `GET` | `/conversations/{id}` | Get conversation metadata |
| `DELETE` | `/conversations/{id}` | Delete a conversation |
| `POST` | `/conversations/{id}/stream` | **SSE streaming endpoint** for multi-agent responses |
| `POST` | `/conversations/hitl` | Resolve Human-in-the-Loop tool execution approval |
| `POST` | `/conversations/{id}/branch` | Branch conversation tree from a specific message ID |

---

## Knowledge & RAG Endpoints (`/files`)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/files` | List uploaded user files |
| `POST` | `/files/upload` | Upload document, parse, chunk, embed, and index into Qdrant |
| `GET` | `/files/{id}/chunks` | Retrieve chunk hierarchy for a file |
| `DELETE` | `/files/{id}` | Delete file record and clean up associated vector embeddings |

---

## FastMCP Tools & Resources (`/mcp`)
Nexus implements the FastMCP standard to expose tools to Claude Desktop and agentic clients:
- **Tools**: `web_search`, `code_execution`, `rag_search`
- **Resources**: `nexus://conversations/{id}`, `nexus://files/{id}`
- **Prompts**: `nexus://prompts/{name}`
