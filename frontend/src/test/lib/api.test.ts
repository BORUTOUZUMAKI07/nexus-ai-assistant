import { describe, it, expect, beforeEach } from "vitest"
import { server } from "@/test/mocks/server"
import { http, HttpResponse } from "msw"
import {
  fetchConversations,
  createConversation,
  deleteConversation,
  loginUser,
  fetchUsage,
  fetchKnowledgeFiles,
  sendHITLFeedback,
} from "@/lib/api"

const API = "/api"

describe("api client", () => {
  beforeEach(() => {
    document.cookie.split(";").forEach((c) => {
      const name = c.split("=")[0].trim()
      document.cookie = `${name}=; path=/; max-age=0`
    })
  })

  it("does not attach an Authorization header client-side (tokens live in httpOnly cookies)", async () => {
    let seenAuth: string | null = "unset"
    server.use(
      http.get(`${API}/conversations`, ({ request }) => {
        seenAuth = request.headers.get("authorization")
        return HttpResponse.json([])
      })
    )
    await fetchConversations()
    expect(seenAuth).toBeNull()
  })

  it("maps conversation responses into a page envelope", async () => {
    const page = await fetchConversations(2, 10)
    expect(page.items).toHaveLength(1)
    expect(page.items[0].id).toBe("conv-100")
    expect(page.total).toBe(1)
    expect(page.page).toBe(2)
    expect(page.size).toBe(10)
  })

  it("retries transient backend-unavailable responses until it succeeds", async () => {
    let calls = 0
    server.use(
      http.get(`${API}/conversations`, () => {
        calls += 1
        return calls < 3
          ? HttpResponse.json({ detail: "backend_unavailable" }, { status: 503 })
          : HttpResponse.json([])
      })
    )
    const page = await fetchConversations(1, 50, { attempts: 3, baseDelayMs: 1 })
    expect(calls).toBe(3)
    expect(page.items).toHaveLength(0)
    expect(page.total).toBe(0)
  })

  it("surfaces real 4xx errors without retrying", async () => {
    let calls = 0
    server.use(
      http.get(`${API}/conversations`, () => {
        calls += 1
        return HttpResponse.json({ detail: "forbidden" }, { status: 403 })
      })
    )
    await expect(
      fetchConversations(1, 50, { attempts: 3, baseDelayMs: 1 })
    ).rejects.toThrow("Fetch conversations failed: 403")
    expect(calls).toBe(1)
  })

  it("silently refreshes on 401 and retries once", async () => {
    let refreshCalls = 0
    let requests = 0
    server.use(
      http.get(`${API}/conversations`, () => {
        requests += 1
        return requests === 1
          ? HttpResponse.json({ detail: "expired" }, { status: 401 })
          : HttpResponse.json([])
      }),
      http.post(`${API}/auth/refresh`, () => {
        refreshCalls += 1
        // Ordinary 200s never reach the browser (httpOnly cookie rotation is
        // server-side), so the retry just reuses the same request.
        return HttpResponse.json({ ok: true })
      })
    )
    const page = await fetchConversations()
    expect(requests).toBe(2)
    expect(refreshCalls).toBe(1)
    expect(page.items).toHaveLength(0)
    expect(page.total).toBe(0)
  })

  it("keeps the 401 and clears the session when the refresh is declined", async () => {
    let logoutCalls = 0
    server.use(
      http.get(`${API}/conversations`, () =>
        HttpResponse.json({ detail: "expired" }, { status: 401 })
      ),
      http.post(`${API}/auth/refresh`, () =>
        HttpResponse.json({ detail: "Refresh token has already been used." }, { status: 401 })
      ),
      http.post(`${API}/auth/logout`, () => {
        logoutCalls += 1
        return HttpResponse.json({ message: "Logged out successfully" })
      })
    )
    await expect(fetchConversations()).rejects.toThrow("Fetch conversations failed: 401")
    expect(logoutCalls).toBe(1)
  })

  it("creates a conversation with a JSON body", async () => {
    let sentBody = ""
    server.use(
      http.post(`${API}/conversations`, async ({ request }) => {
        sentBody = await request.text()
        return HttpResponse.json({
          id: "conv-new-1",
          title: "New Conversation",
          mode: "code",
          created_at: "",
          updated_at: "",
          message_count: 0,
        })
      })
    )
    const created = await createConversation("My title", "code")
    expect(created.id).toBe("conv-new-1")
    expect(sentBody).toContain("My title")
    expect(sentBody).toContain('"mode":"code"')
  })

  it("resolves after a 204 on delete", async () => {
    await expect(deleteConversation("conv-100")).resolves.toBeUndefined()
  })

  it("reports success on login without exposing tokens to the page", async () => {
    const res = await loginUser({ email: "test@nexus.ai", password: "password123" })
    expect(res.ok).toBe(true)
  })

  it("throws the backend detail on failed login", async () => {
    await expect(
      loginUser({ email: "test@nexus.ai", password: "wrong" })
    ).rejects.toThrow("Incorrect email or password.")
  })

  it("fetches the usage summary envelope", async () => {
    const usage = await fetchUsage()
    expect(usage.total_tokens).toBe(142850)
    expect(usage.total_requests).toBe(42)
    expect(usage.prompt_tokens).toBe(98420)
    expect(usage.cached_tokens).toBe(25110)
  })

  it("lists knowledge files", async () => {
    const files = await fetchKnowledgeFiles()
    expect(files[0].filename).toBe("nexus-spec.pdf")
    expect(files[0].status).toBe("indexed")
  })

  it("posts HITL feedback", async () => {
    const res = await sendHITLFeedback({ threadId: "t-1", action: "approve" })
    expect(res.status).toBe("received")
  })
})