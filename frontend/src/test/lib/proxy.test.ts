import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { backendFetch } from "@/lib/proxy"

const { getCookie, getHeader } = vi.hoisted(() => ({
  getCookie: vi.fn(),
  getHeader: vi.fn(),
}))

// `headers` is mocked alongside `cookies` because backendFetch reads the Origin
// header on mutating requests for the CSRF check. Mocking only `cookies` made
// Vitest throw "No 'headers' export is defined on the 'next/headers' mock" the
// moment any POST test ran.
vi.mock("next/headers", () => ({
  cookies: () => ({ get: getCookie }),
  headers: () => ({ get: getHeader }),
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
    // Default to a same-origin POST so the CSRF check is inert unless a test is
    // specifically exercising it. An absent Origin means "no cross-site
    // initiator", which is how same-origin fetches and the server's own calls
    // look.
    getHeader.mockReturnValue(undefined)
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

  it("always bounds an upstream call with an abort signal", async () => {
    // Without a signal, a backend that accepts the connection and then stalls
    // holds a Node request slot open forever. Every JSON route goes through here,
    // so this is asserted once at the single choke point rather than 40 times.
    const fetchMock = stubFetch(200)
    await backendFetch("/usage/summary")
    expect(fetchMock.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal)
  })

  it("preserves a caller-supplied signal instead of overwriting it", async () => {
    // The streaming chat route forwards the client abort so Stop cancels the
    // upstream LLM call. A blanket timeout must not clobber that.
    const fetchMock = stubFetch(200)
    const caller = new AbortController()
    await backendFetch("/usage/summary", { signal: caller.signal })
    expect(fetchMock.mock.calls[0][1].signal).toBe(caller.signal)
  })
})

describe("backendFetch cross-origin rejection (CSRF)", () => {
  beforeEach(() => {
    getCookie.mockReturnValue({ value: "abc.def.ghi" })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  /** Answer the Origin and Host lookups the check performs. */
  function stubOrigin(origin: string | undefined, host: string | undefined) {
    getHeader.mockImplementation((name: string) =>
      name === "origin" ? origin : host
    )
  }

  it("rejects a mutating request whose Origin is not this host", async () => {
    const fetchMock = stubFetch(200)
    stubOrigin("https://evil.example", "nexus.example")
    const res = await backendFetch("/conversations", { method: "POST" })
    expect(res.status).toBe(403)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("allows a mutating request that is same-origin", async () => {
    const fetchMock = stubFetch(200)
    stubOrigin("https://nexus.example", "nexus.example")
    const res = await backendFetch("/conversations", { method: "POST" })
    expect(res.status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("compares against x-forwarded-host when behind a proxy", async () => {
    // Deployments terminate TLS upstream, so Host is the internal address and
    // the public one only appears in x-forwarded-host.
    const fetchMock = stubFetch(200)
    getHeader.mockImplementation((name: string) => {
      if (name === "origin") return "https://nexus.example"
      if (name === "x-forwarded-host") return "nexus.example"
      return undefined
    })
    const res = await backendFetch("/conversations", { method: "DELETE" })
    expect(res.status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("allows mutating requests with no Origin header at all", async () => {
    // Browsers omit Origin for same-origin navigations, and the server's own
    // internal calls have none. Rejecting these would break the app itself.
    const fetchMock = stubFetch(200)
    stubOrigin(undefined, "nexus.example")
    const res = await backendFetch("/conversations", { method: "PATCH" })
    expect(res.status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("does not apply the check to reads", async () => {
    // Every GET handler in the app is read-only, and a cross-site top-level
    // navigation does carry cookies under SameSite=Lax, so gating reads would
    // break ordinary link-clicks without adding protection.
    const fetchMock = stubFetch(200)
    stubOrigin("https://evil.example", "nexus.example")
    const res = await backendFetch("/conversations")
    expect(res.status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("does not treat an unparseable Origin as an attack", async () => {
    // A malformed value from some proxy must not be usable to lock a user out
    // of their own app.
    const fetchMock = stubFetch(200)
    stubOrigin("not-a-url", "nexus.example")
    const res = await backendFetch("/conversations", { method: "POST" })
    expect(res.status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})