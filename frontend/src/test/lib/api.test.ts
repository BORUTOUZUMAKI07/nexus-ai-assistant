import { describe, it, expect, beforeEach } from "vitest"
import { server } from "@/test/mocks/server"
import { http, HttpResponse } from "msw"
import { setAccessToken, clearAccessToken } from "@/lib/auth"
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

  it("adds an Authorization header when a token cookie exists", async () => {
    let seenAuth: string | null = null
    server.use(
      http.get(`${API}/conversations`, ({ request }) => {
        seenAuth = request.headers.get("authorization")
        return HttpResponse.json([], { status: 401 })
      })
    )
    setAccessToken("tok-123")
    await fetchConversations().catch(() => {})
    expect(seenAuth).toBe("Bearer tok-123")
  })

  it("does not add an Authorization header when logged out", async () => {
    let seenAuth: string | null = "unset"
    server.use(
      http.get(`${API}/conversations`, ({ request }) => {
        seenAuth = request.headers.get("authorization")
        return HttpResponse.json([])
      })
    )
    clearAccessToken()
    await fetchConversations()
    expect(seenAuth).toBeNull()
  })

  it("maps conversation responses into a page envelope", async () => {
    setAccessToken("tok")
    const page = await fetchConversations(2, 10)
    expect(page.items).toHaveLength(1)
    expect(page.items[0].id).toBe("conv-100")
    expect(page.total).toBe(1)
    expect(page.page).toBe(2)
    expect(page.size).toBe(10)
  })

  it("creates a conversation with a JSON body", async () => {
    let sentBody = ""
    server.use(
      http.post(`${API}/conversations`, async ({ request }) => {
        sentBody = await request.text()
        return HttpResponse.json({
          id: "conv-new-1",
          title: "New Conversation",
          mode: "normal",
          created_at: "",
          updated_at: "",
          message_count: 0,
        })
      })
    )
    setAccessToken("tok")
    const created = await createConversation("My title", "code")
    expect(created.id).toBe("conv-new-1")
    expect(sentBody).toContain("My title")
    expect(sentBody).toContain('"mode":"code"')
  })

  it("resolves after a 204 on delete", async () => {
    setAccessToken("tok")
    await expect(deleteConversation("conv-100")).resolves.toBeUndefined()
  })

  it("returns a token pair on successful login", async () => {
    const res = await loginUser({ email: "test@nexus.ai", password: "password123" })
    expect(res.access_token).toBe("test-access-token")
    expect(res.refresh_token).toBe("test-refresh-token")
  })

  it("throws the backend detail on failed login", async () => {
    await expect(
      loginUser({ email: "test@nexus.ai", password: "wrong" })
    ).rejects.toThrow("Incorrect email or password.")
  })

  it("fetches usage summary with a default period fallback", async () => {
    const usage = await fetchUsage("7d")
    expect(usage.request_count).toBe(42)
    expect(usage.period).toBe("30d")
  })

  it("lists knowledge files", async () => {
    setAccessToken("tok")
    const files = await fetchKnowledgeFiles()
    expect(files[0].filename).toBe("nexus-spec.pdf")
    expect(files[0].status).toBe("indexed")
  })

  it("posts HITL feedback", async () => {
    setAccessToken("tok")
    const res = await sendHITLFeedback({ threadId: "t-1", action: "approve" })
    expect(res.status).toBe("received")
  })
})