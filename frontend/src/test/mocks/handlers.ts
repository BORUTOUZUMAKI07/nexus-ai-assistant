import { http, HttpResponse, delay } from "msw"

export const mockConversations = [
  {
    id: "conv-100",
    title: "Project kickoff",
    model: "llama-3.3-70b-versatile",
    is_pinned: true,
    is_archived: false,
    token_count: 12850,
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-02T10:00:00Z",
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

export const mockSettings = {
  id: "st-1",
  user_id: "user-1",
  theme: "dark",
  default_model: "llama-3.3-70b-versatile",
  system_prompt_override: "You are Nexus AI, a precise production assistant.",
  temperature: 0.7,
  max_tokens: 8192,
  stream_response: true,
  enable_memory: true,
  enable_tools: true,
  custom_settings: {},
}

export const mockMemories = [
  {
    id: "mem-1",
    user_id: "user-1",
    content: "Prefers Python and TypeScript for backend work",
    category: "preference",
    confidence: 0.9,
    source_conversation_id: null,
    is_active: true,
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-01T10:00:00Z",
  },
]

export const mockAPIKeys = [
  {
    id: "key-1",
    user_id: "user-1",
    provider: "groq",
    key_preview: "gsk_• • • • 1234",
    label: "Groq production",
    is_active: true,
    created_at: "2026-09-01T10:00:00Z",
  },
]

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

  http.post("/api/auth/refresh", ({ request }) => {
    void request
    return HttpResponse.json({
      access_token: "test-refreshed-access-token",
      refresh_token: "test-refreshed-refresh-token",
    })
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
      model: "llama-3.3-70b-versatile",
      is_pinned: false,
      is_archived: false,
      token_count: 0,
      created_at: "2026-09-06T00:00:00Z",
      updated_at: "2026-09-06T00:00:00Z",
    })
  }),

  http.get("/api/conversations/:id", () =>
    HttpResponse.json({
      ...mockConversations[0],
      system_prompt: null,
      messages: [
        {
          id: "msg-1",
          role: "user",
          content: "Tell me about RAG",
          thought_process: null,
          model: null,
          prompt_tokens: 12,
          completion_tokens: 0,
          total_tokens: 12,
          citations: [],
          tool_calls: [],
          user_feedback: null,
          created_at: "2026-09-02T10:00:00Z",
        },
        {
          id: "msg-2",
          role: "assistant",
          content: "RAG grounds answers in your indexed documents.",
          thought_process: "Use hybrid search to find relevant chunks.",
          model: "llama-3.3-70b-versatile",
          prompt_tokens: 12,
          completion_tokens: 8,
          total_tokens: 20,
          citations: [],
          tool_calls: [],
          user_feedback: null,
          created_at: "2026-09-02T10:00:01Z",
        },
      ],
    })
  ),

  http.patch("/api/conversations/:id", ({ request }) => {
    if (!requireAuth(request)) {
      return HttpResponse.json({ detail: "Not authenticated" }, { status: 401 })
    }
    return HttpResponse.json(mockConversations[0])
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
      prompt_tokens: 98420,
      completion_tokens: 44430,
      cached_tokens: 25110,
      total_cost_usd: 0.0417,
      total_requests: 42,
      average_latency_ms: 872,
    })
  ),

  // ── Settings ────────────────────────────────────────────────────────────
  http.get("/api/settings", () => HttpResponse.json(mockSettings)),
  http.put("/api/settings", async ({ request }) => {
    const body = (await request.json()) as Partial<typeof mockSettings>
    return HttpResponse.json({ ...mockSettings, ...body })
  }),
  http.get("/api/settings/memories", () => HttpResponse.json(mockMemories)),
  http.post("/api/settings/memories", async ({ request }) => {
    const body = (await request.json()) as { content: string; category?: string }
    return HttpResponse.json({
      ...mockMemories[0],
      id: "mem-new-1",
      content: body.content,
      category: body.category ?? "preference",
    })
  }),
  http.delete("/api/settings/memories/:id", () =>
    HttpResponse.json(null, { status: 204 })
  ),
  http.get("/api/settings/keys", () => HttpResponse.json(mockAPIKeys)),
  http.post("/api/settings/keys", async ({ request }) => {
    const body = (await request.json()) as { provider: string; label?: string }
    return HttpResponse.json({
      ...mockAPIKeys[0],
      id: "key-new-1",
      provider: body.provider,
      key_preview: "• • • • 0000",
      label: body.label ?? null,
    })
  }),

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

  // ── Admin (proxied to backend /api/v1/admin/*) ──────────────────────────
  http.get("/api/admin/users", () =>
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
  http.post("/api/admin/users/:id/toggle-status", () =>
    HttpResponse.json({ detail: "ok" })
  ),
  http.get("/api/admin/system-status", () =>
    HttpResponse.json({ status: "healthy", database: "connected", redis_cache: "connected" })
  ),
  http.get("/api/admin/audit-logs", () =>
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