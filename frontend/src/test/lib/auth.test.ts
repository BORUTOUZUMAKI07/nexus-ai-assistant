import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import {
  checkAuth,
  clearSession,
  getAccessToken,
  TOKEN_COOKIE,
  SESSION_EXPIRED_EVENT,
} from "@/lib/auth"

describe("auth session helper", () => {
  beforeEach(() => {
    document.cookie.split(";").forEach((c) => {
      const name = c.split("=")[0].trim()
      document.cookie = `${name}=; path=/; max-age=0`
    })
    vi.unstubAllGlobals()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("returns null when no token cookie is present", () => {
    expect(getAccessToken()).toBeNull()
  })

  it("reads a (non-httpOnly) token cookie as a best-effort compat helper", () => {
    document.cookie = `${TOKEN_COOKIE}=abc.def.ghi; path=/`
    expect(getAccessToken()).toBe("abc.def.ghi")
  })

  it("URL-decodes special characters in a token", () => {
    const token = "eyJhbGciOiJIUzI1NiJ9.+/="
    document.cookie = `${TOKEN_COOKIE}=${encodeURIComponent(token)}; path=/`
    expect(getAccessToken()).toBe(token)
  })

  it("checkAuth resolves true when the /api/auth/me probe succeeds", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ authenticated: true }), { status: 200 })))
    await expect(checkAuth()).resolves.toBe(true)
  })

  it("checkAuth resolves false when the probe is rejected (401)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ authenticated: false }), { status: 401 })))
    await expect(checkAuth()).resolves.toBe(false)
  })

  it("checkAuth resolves FALSE on an anonymous 200 probe (the regression: authenticated flag lives in the body)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ authenticated: false }), { status: 200 })))
    await expect(checkAuth()).resolves.toBe(false)
  })

  it("checkAuth resolves false when the probe itself fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network down")))
    await expect(checkAuth()).resolves.toBe(false)
  })

  it("clearSession posts to /api/auth/logout so httpOnly cookies are cleared server-side", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }))
    vi.stubGlobal("fetch", fetchMock)
    await clearSession()
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/logout", { method: "POST" })
  })

  it("clearSession never throws, even when the backend is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("down")))
    await expect(clearSession()).resolves.toBeUndefined()
  })

  it("exposes the session-expired event name for cross-module coordination", () => {
    expect(SESSION_EXPIRED_EVENT).toBe("nexus:session-expired")
  })
})