import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import {
  checkAuth,
  clearSession,
  SESSION_EXPIRED_EVENT,
} from "@/lib/auth"

describe("auth session helper", () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
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