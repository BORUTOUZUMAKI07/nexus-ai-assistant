import { http, HttpResponse, delay } from "msw"

export const mockConversations = [
  {
    id: "conv-100",
    title: "Project kickoff",
    mode: "normal",
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-02T10:00:00Z",
    message_count: 5,
  },
]

export const mockTokenPair = {
  access_token: "test-access-token",
  refresh_token: "test-refresh-token",
  token_type: "bearer",
}

export const mockUser = {
  id: "user-1",
  email: "test@nexus.ai",
  username: "tester",
  full_name: "Tester",
  is_active: true,
  created_at: "2026-09-01T10:00:00Z",
}

// Vercel AI SDK data-stream protocol body for /api/chat
export function chatStreamBody(
  textFragments = ["Hello from Nexus."],
  annotations: unknown[] = []
): string {
  const lines: string[] = []
  for (const fragment of textFragments) {
    lines.push(`0:${JSON.stringify(fragment)}`)
  }
  for (const annotation of annotations) {
    lines.push(`8:[${JSON.stringify(annotation)}]`)
  }
  return `${lines.join("\n")}\n`
}

function requireAuth(request: Request): boolean {
  return Boolean(request.headers.get("authorization")?.startsWith("Bearer "))
}

export const handlers = [
  // ── Auth ────────────────────────────────────────────────────────────────
  http.post("/api/auth/login", async ({ request }) => {
    const contentType = request.headers.get("content-type") ?? ""
    let password = ""
    let email = ""
    if (contentType.includes("application/x-www-form-urlencoded")) {
      const params = new URLSearchParams(await request.text())
      email = params.get("username") ?? ""
      password = params.get("password") ?? ""
    } else {
      const body = (await request.json()) as { email?: string; password?: string }
      email = body.email ?? ""
      password = body.password ?? ""
    }
    if (!email || password !== "password123") {
      return HttpResponse.json(
        { detail: "Incorrect email or password." },
        { status: 401 }
      )
    }
    return HttpResponse.json(mockTokenPair)
  }),

  http.post("/api/auth/register", () => HttpResponse.json(mockUser)),

  // ── Conversations ───────────────────────────────────────────────────────
  http.get("/api/conversations", ({ request }) => {
    if (!requireAuth(request)) {
      return HttpResponse.json({ detail: "Not authenticated" }, { status: 401 })
    }
    return HttpResponse.json(mockConversations)
  }),

  http.post("/api/conversations", ({ request }) => {
    if (!requireAuth(request)) {
      return HttpResponse.json({ detail: "Not authenticated" }, { status: 401 })
    }
    return HttpResponse.json({
      id: "conv-new-1",
      title: "New Conversation",
      mode: "normal",
      created_at: "2026-09-06T00:00:00Z",
      updated_at: "2026-09-06T00:00:00Z",
      message_count: 0,
    })
  }),

  http.delete("/api/conversations/:id", () =>
    HttpResponse.json(null, { status: 204 })
  ),

  // ── Knowledge / Files ───────────────────────────────────────────────────
  http.get("/api/files", () =>
    HttpResponse.json([
      {
        id: "file-1",
        filename: "nexus-spec.pdf",
        status: "indexed",
        size_bytes: 145200,
        chunk_count: 42,
        created_at: "2026-09-01T10:00:00Z",
      },
    ])
  ),

  http.post("/api/files/upload", () => {
    delay(10)
    return HttpResponse.json({
      id: "file-2",
      filename: "uploaded.txt",
      status: "indexed",
      size_bytes: 1200,
      chunk_count: 1,
      created_at: "2026-09-06T00:00:00Z",
    })
  }),

  http.delete("/api/files/:id", () => HttpResponse.json(null, { status: 204 })),

  http.post("/api/files/rag/query", () =>
    HttpResponse.json({
      citations: [
        {
          filename: "nexus-spec.pdf",
          chunk_index: 4,
          score: 0.94,
          content_snippet: "Hybrid Search combines dense embeddings and sparse BM25.",
        },
      ],
    })
  ),

  // ── Usage ───────────────────────────────────────────────────────────────
  http.get("/api/usage/summary", () =>
    HttpResponse.json({
      total_tokens: 142850,
      input_tokens: 98420,
      output_tokens: 44430,
      total_cost_usd: 0,
      request_count: 42,
      period: "30d",
    })
  ),

  // ── HITL ────────────────────────────────────────────────────────────────
  http.post("/api/hitl", () => HttpResponse.json({ status: "received" })),

  // ── Chat stream (Vercel AI SDK data-stream) ─────────────────────────────
  http.post("/api/chat", async () => {
    await delay(10)
    return new HttpResponse(chatStreamBody(), {
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        "X-Vercel-AI-Data-Stream": "v1",
      },
    })
  }),

  // ── Admin (browser hits /api/v1 directly) ──────────────────────────────
  http.get("/api/v1/admin/users", () =>
    HttpResponse.json([
      {
        id: "usr-01",
        email: "admin@nexus.ai",
        username: "admin",
        full_name: "Lead Administrator",
        role: "admin",
        is_active: true,
        created_at: "2026-09-01T10:00:00Z",
      },
    ])
  ),
  http.post("/api/v1/admin/users/:id/toggle-status", () =>
    HttpResponse.json({ detail: "ok" })
  ),
  http.get("/api/v1/admin/system-status", () =>
    HttpResponse.json({ status: "healthy", database: "connected", redis_cache: "connected" })
  ),
  http.get("/api/v1/admin/audit-logs", () =>
    HttpResponse.json([
      {
        id: "log-1",
        action: "AUTH_LOGIN",
        resource_type: "user",
        status: "success",
        ip_address: "127.0.0.1",
        created_at: "2026-09-06T00:00:00Z",
      },
    ])
  ),
]