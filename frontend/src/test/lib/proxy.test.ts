import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { backendFetch } from "@/lib/proxy"

const { getCookie } = vi.hoisted(() => ({ getCookie: vi.fn() }))

vi.mock("next/headers", () => ({
  cookies: () => ({ get: getCookie }),
}))

function stubFetch(status: number, body = "{}") {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(body, {
      status,
      headers: { "Content-Type": "application/json" },
    })
  )
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

describe("backendFetch bearer-forwarding", () => {
  beforeEach(() => {
    getCookie.mockReturnValue({ value: "abc.def.ghi" })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it("forwards the bearer token for /auth/me (the session probe)", async () => {
    const fetchMock = stubFetch(200)
    await backendFetch("/auth/me", { method: "GET" })
    expect(fetchMock.mock.calls[0][1].headers.get("Authorization")).toBe(
      "Bearer abc.def.ghi"
    )
  })

  it("forwards the bearer token for regular authed routes", async () => {
    const fetchMock = stubFetch(200)
    await backendFetch("/conversations", { method: "GET" })
    expect(fetchMock.mock.calls[0][1].headers.get("Authorization")).toBe(
      "Bearer abc.def.ghi"
    )
  })

  it("never forwards a stale token to /auth/login or /auth/refresh", async () => {
    const loginFetch = stubFetch(200)
    await backendFetch("/auth/login", { method: "POST" })
    expect(loginFetch.mock.calls[0][1].headers.get("Authorization")).toBeNull()

    const refreshFetch = stubFetch(200)
    await backendFetch("/auth/refresh", { method: "POST" })
    expect(refreshFetch.mock.calls[0][1].headers.get("Authorization")).toBeNull()
  })

  it("targets the /api/v1 backend base URL", async () => {
    const fetchMock = stubFetch(200)
    await backendFetch("/usage/summary")
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://127.0.0.1:8000/api/v1/usage/summary"
    )
  })
})