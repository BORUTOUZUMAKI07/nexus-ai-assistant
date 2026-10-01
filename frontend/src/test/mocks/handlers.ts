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

export const mockPlan = {
  id: "plan-1",
  conversation_id: "conv-100",
  user_id: "user-1",
  title: "Refactor auth service",
  summary: "Split the session manager out of the auth service.",
  steps: ["Extract SessionManager", "Add unit tests", "Wire DI container"],
  status: "pending",
  decision_reason: null,
  created_at: "2026-09-06T00:00:00Z",
  updated_at: "2026-09-06T00:00:00Z",
  decided_at: null,
}

export const mockArtifacts = [
  {
    id: "art-1",
    user_id: "user-1",
    conversation_id: "conv-100",
    message_id: null,
    title: "auth.py",
    language: "python",
    mime_type: "text/x-python",
    content: "def auth():\n    return True\n",
    version: 2,
    created_at: "2026-09-06T00:00:00Z",
    updated_at: "2026-09-06T00:00:00Z",
  },
]

export const mockHookPolicies = [
  {
    id: "hook-1",
    name: "Block shell exec",
    tool_name: "run_shell",
    event: "pre_tool",
    org_id: null,
    action: "block",
    field: null,
    message: "Shell execution is disabled for safety",
    enabled: true,
    created_at: "2026-09-06T00:00:00Z",
    updated_at: "2026-09-06T00:00:00Z",
  },
]

export const mockSliceReport = {
  overall: {
    requests: 22,
    error_rate: 0.0455,
    helpful_rate: 0.7727,
    avg_latency_ms: 214.3,
    combined_cost_usd: 0.0009,
  },
  slices: [
    {
      model: "alpha",
      provider: "groq",
      volume: 10,
      rank: 1,
      popularity_bucket: "popular",
      error_rate: 0,
      avg_latency_ms: 180,
      cost_usd: 0.0004,
      helpful_rate: 0.5,
      flags: ["high_popularity_low_quality"],
    },
    {
      model: "beta",
      provider: "groq",
      volume: 8,
      rank: 2,
      popularity_bucket: "moderate",
      error_rate: 0.125,
      avg_latency_ms: 250,
      cost_usd: 0.0003,
      helpful_rate: 1,
      flags: [],
    },
    {
      model: "gamma",
      provider: "openai",
      volume: 4,
      rank: 3,
      popularity_bucket: "long_tail",
      error_rate: 0,
      avg_latency_ms: 320,
      cost_usd: 0.0002,
      helpful_rate: 1,
      flags: [],
    },
  ],
  popularity_flags: [
    {
      slice: "alpha / groq",
      helpful_rate: 0.5,
      flags: ["high_popularity_low_quality"],
    },
  ],
}

export const mockFairnessReport = {
  evaluator_parity: [
    { group: "deepeval / faithfulness", count: 20, pass_rate: 0.85 },
    { group: "ragas / answer_relevancy", count: 8, pass_rate: 0.75 },
  ],
  model_parity: [
    { model: "alpha", count: 12, pass_rate: 0.75 },
    { model: "beta", count: 10, pass_rate: 0.9 },
  ],
  provider_error_parity: [
    { provider: "groq", count: 18, error_rate: 0.0556, flagged: false },
    { provider: "openai", count: 4, error_rate: 0, flagged: false },
  ],
  limitations:
    "Fairness surface is deliberately slim (population-level parity only). Protected-attribute cohorts are not collected, so statistical-parity claims are out of scope; treat these numbers as an early-warning signal, not an audit conclusion.",
}

export const mockBanditStatus = {
  epsilon: 0.1,
  exploration: "ε-greedy",
  stats: [
    { experiment: "chat_system_prompt", variant: "control", reward_count: 41, mean_reward: 0.6341 },
    { experiment: "chat_system_prompt", variant: "canary_v1", reward_count: 38, mean_reward: 0.7895 },
  ],
}

export const mockOptimizationRuns = [
  {
    id: "opt-run-1",
    prompt_key: "chat_system_prompt",
    status: "completed",
    candidate_count: 4,
    accepted_variant: "You are a precise, evidence-first assistant.",
    baseline_score: 0.625,
    best_score: 0.8125,
    average_score: 0.75,
    promoted: true,
    created_at: "2026-09-25T09:12:00Z",
  },
]

export const mockAuditReport = {
  controls: {
    pii_redaction_enabled: true,
    response_cache_enabled: false,
    rate_limit_per_minute: 60,
    totp_available: true,
  },
  lifecycle_hooks: { policy_count: 1, enabled: 1, block_policies: 1 },
  model_provenance: [
    {
      model: "alpha",
      requests: 10,
      providers: ["groq"],
      experiment_variants_seen: ["canary_v1"],
    },
  ],
  prompt_provenance: { version_count: 3, latest_timestamp: "2026-09-24T16:00:00Z" },
  red_team: {
    run_count: 4,
    last_run_at: "2026-09-26T08:00:00Z",
    total_probes: 12,
    blocked_probes: 9,
    defense_rate: 0.75,
  },
  gdpr: { gdpr_export: 2, gdpr_erasure: 1 },
  evaluations: [{ evaluator: "deepeval", count: 20, pass_rate: 0.85 }],
  retention:
    "Chat data is retained for the active account lifecycle and erased on right-to-erasure; logs keep audit metadata per industry norms. Response cache TTL: 3600s (disabled).",
  eu_ai_act: {
    classification: "downstream provider of a general-purpose chatbot",
    high_risk_articles:
      "Not directly subject to Art 51-55 (not a high-risk deployer under Annex III for this interface)",
    transparency_obligations: {
      art_50: "Limited-risk chatbot transparency obligations apply since 2026-08-02",
      disclosure: "Users should be disclosed that they interact with an AI system",
    },
    gpaI_models: {
      role: "Downstream provider consuming GPAI models from upstream providers",
      upstream_obligations:
        "Art 53 GPAI obligations have applied since 2025-08-02; on-market GPAI models must comply by 2027-08-02",
    },
    fines: "Up to EUR 15M or 3% of global annual turnover for non-compliance with applicable obligations",
    internal_evidence: {
      iso_iec_42001:
        "Documented management-system evidence is reusable as control evidence but is NOT Art 17-equivalent",
      status_date: "2026-09-27",
    },
  },
}

export const mockRedTeamRuns = [
  {
    id: "rt-1",
    created_at: "2026-09-26T08:00:00Z",
    total_probes: 12,
    blocked_probes: 9,
    defense_rate: 0.75,
    probe_count: 12,
  },
  {
    id: "rt-2",
    created_at: "2026-09-20T08:00:00Z",
    total_probes: 10,
    blocked_probes: 6,
    defense_rate: 0.6,
    probe_count: 10,
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
    // Mirrors the real /api/auth/login route handler, which echoes only a
    // boolean and keeps the tokens in httpOnly cookies server-side.
    return HttpResponse.json({ ok: true })
  }),

  // MUST stay above the `:provider` handler below. MSW matches in registration
  // order, exactly like the App Router and like FastAPI, so a literal path
  // registered after a `:param` sibling is shadowed. Both the backend route and
  // this mock are ordered for the same reason; putting this one lower would
  // make the sign-in page believe the IdP is called "providers".
  http.get("/api/auth/oauth/providers", () =>
    HttpResponse.json({
      providers: [
        { name: "google", configured: true },
        { name: "github", configured: true },
      ],
    })
  ),

  http.get("/api/auth/oauth/:provider", () =>
    HttpResponse.json({
      authorization_url:
        "https://idp.example/authorize?response_type=code&client_id=nexus-test&state=test-sso-state&code_challenge_method=S256",
      state: "test-sso-state",
      provider: "oidc",
    })
  ),

  // IdP redirect destination: the proxy route writes httpOnly cookies and
  // bounces to /app — no JSON body reaches the page script.
  http.get("/api/auth/oauth/:provider/callback", () => HttpResponse.json({})),

  http.post("/api/auth/refresh", () =>
    HttpResponse.json({
      ok: true,
    })
  ),

  http.post("/api/auth/register", () => HttpResponse.json(mockUser)),

  // The browser never sends Authorization headers — tokens live in httpOnly
  // cookies that the Next proxy attaches server-side. Normally these requests
  // are "authenticated", so handlers below don't gate on a header.
  http.get("/api/auth/me", () =>
    // A signed-in page reads the httpOnly cookie; jsdom has none, so the gate
    // opens (authenticated: false) exactly like a cold-start browser.
    HttpResponse.json({ authenticated: false })
  ),

  http.post("/api/auth/logout", () =>
    HttpResponse.json({ message: "Logged out successfully" })
  ),

  // ── Conversations ───────────────────────────────────────────────────────
  http.get("/api/conversations", () => HttpResponse.json(mockConversations)),

  http.post("/api/conversations", () =>
    HttpResponse.json({
      id: "conv-new-1",
      title: "New Conversation",
      model: "llama-3.3-70b-versatile",
      is_pinned: false,
      is_archived: false,
      token_count: 0,
      created_at: "2026-09-06T00:00:00Z",
      updated_at: "2026-09-06T00:00:00Z",
    })
  ),

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

  http.patch("/api/conversations/:id", async ({ request }) => {
    // Echo the patch back so a test can assert the rename actually persisted
    // what it sent, instead of asserting against a static fixture.
    const patch = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({ ...mockConversations[0], ...patch });
  }),

  // Forks were previously unmocked, which made the sidebar's fork button
  // untestable: MSW let the request fall through to a server that does not
  // exist under jsdom. The shape mirrors the real endpoint, which returns a
  // new conversation row.
  http.post("/api/conversations/:id/fork", async ({ request, params }) => {
    const body = (await request.json()) as {
      fork_message_id?: string;
      branch_name?: string;
    };
    return HttpResponse.json({
      ...mockConversations[0],
      id: `conv-forked-${params.id}`,
      title: body.branch_name ?? "Forked Branch",
      is_pinned: false,
      // The fork starts at the branch point, so it carries less history.
      token_count: 0,
    });
  }),

  http.post(
    "/api/conversations/:id/messages/:messageId/feedback",
    async ({ request, params }) => {
      const body = (await request.json()) as {
        feedback?: string;
        feedback_note?: string | null;
      };
      return HttpResponse.json({
        status: "success",
        message_id: params.messageId,
        feedback: body.feedback,
      });
    }
  ),

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

  // Transcription was unmocked, so the voice-input path could only ever be
  // exercised by hitting a nonexistent server. The real endpoint returns a
  // single `text` field.
  http.post("/api/audio/transcribe", () =>
    HttpResponse.json({ text: "How do I add a new conversation hook?" })
  ),

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

  // ── Plans (plan-then-approve) ───────────────────────────────────────────
  http.post("/api/conversations/:id/plan", async ({ request }) => {
    const body = (await request.json()) as { task?: string }
    return HttpResponse.json({
      ...mockPlan,
      id: "plan-new-1",
      title: body.task?.slice(0, 60) ?? mockPlan.title,
    })
  }),

  http.get("/api/conversations/:id/plans", () => HttpResponse.json([mockPlan])),

  http.post("/api/plans/:id/approve", () =>
    HttpResponse.json({
      ...mockPlan,
      status: "approved",
      decided_at: new Date().toISOString(),
    })
  ),

  http.post("/api/plans/:id/reject", () =>
    HttpResponse.json({
      ...mockPlan,
      status: "rejected",
      decided_at: new Date().toISOString(),
    })
  ),

  // ── Artifacts (persisted + versioned) ───────────────────────────────────
  http.get("/api/artifacts", () => HttpResponse.json(mockArtifacts)),

  http.post("/api/artifacts", async ({ request }) => {
    const body = (await request.json()) as {
      title?: string
      language?: string
      content?: string
    }
    return HttpResponse.json({
      ...mockArtifacts[0],
      id: "art-new-1",
      title: body.title ?? mockArtifacts[0].title,
      language: body.language ?? mockArtifacts[0].language,
      content: body.content ?? mockArtifacts[0].content,
      version: 1,
    })
  }),

  http.get("/api/artifacts/:id", () =>
    HttpResponse.json({
      ...mockArtifacts[0],
      versions: [{ ...mockArtifacts[0], version: 1 }],
    })
  ),

  http.post("/api/artifacts/:id/versions", () =>
    HttpResponse.json({
      ...mockArtifacts[0],
      version: mockArtifacts[0].version + 1,
    })
  ),

  http.delete("/api/artifacts/:id", () =>
    HttpResponse.json(null, { status: 204 })
  ),

  // ── Admin: lifecycle hooks ──────────────────────────────────────────────
  http.get("/api/admin/hooks", () => HttpResponse.json(mockHookPolicies)),

  http.post("/api/admin/hooks", async ({ request }) => {
    const body = (await request.json()) as {
      name?: string
      tool_name?: string
      action?: string
    }
    return HttpResponse.json({
      ...mockHookPolicies[0],
      id: "hook-new-1",
      name: body.name ?? mockHookPolicies[0].name,
      tool_name: body.tool_name ?? mockHookPolicies[0].tool_name,
      action: body.action ?? mockHookPolicies[0].action,
    })
  }),

  http.put("/api/admin/hooks/:id", async ({ request }) => {
    const body = (await request.json()) as { enabled?: boolean }
    return HttpResponse.json({
      ...mockHookPolicies[0],
      enabled: body.enabled ?? mockHookPolicies[0].enabled,
    })
  }),

  http.delete("/api/admin/hooks/:id", () =>
    HttpResponse.json(null, { status: 204 })
  ),

  // ── Admin: slice monitoring / fairness / bandits ─────────────────────────
  http.get("/api/admin/monitoring/slices", () => HttpResponse.json(mockSliceReport)),
  http.get("/api/admin/monitoring/fairness", () => HttpResponse.json(mockFairnessReport)),
  http.get("/api/admin/monitoring/bandits", () => HttpResponse.json(mockBanditStatus)),

  // ── Admin: prompt-optimization run + evidence trail ──────────────────────
  http.get("/api/admin/optimization/runs", () => HttpResponse.json(mockOptimizationRuns)),
  http.post("/api/admin/optimization/run", async ({ request }) => {
    const body = (await request.json()) as { baseline_prompt?: string }
    return HttpResponse.json(
      {
        ...mockOptimizationRuns[0],
        id: "opt-run-new-1",
        baseline_prompt: body.baseline_prompt ?? mockOptimizationRuns[0].baseline_score,
        created_at: new Date().toISOString(),
      },
      { status: 201 }
    )
  }),

  // ── Admin: responsible-ML / compliance audit surface ─────────────────────
  http.get("/api/admin/audit", () => HttpResponse.json(mockAuditReport)),
  http.get("/api/admin/audit/redteam", () =>
    HttpResponse.json({ runs: mockRedTeamRuns })
  ),
]