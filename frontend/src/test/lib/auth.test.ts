import { describe, it, expect, beforeEach } from "vitest"
import {
  getAccessToken,
  setAccessToken,
  clearAccessToken,
  getRefreshToken,
  setRefreshToken,
  clearRefreshToken,
  setSession,
  clearSession,
  TOKEN_COOKIE,
} from "@/lib/auth"

describe("auth token store", () => {
  beforeEach(() => {
    document.cookie.split(";").forEach((c) => {
      const name = c.split("=")[0].trim()
      document.cookie = `${name}=; path=/; max-age=0`
    })
  })

  it("returns null when no token cookie is present", () => {
    expect(getAccessToken()).toBeNull()
  })

  it("round-trips a plain token through the cookie", () => {
    setAccessToken("abc.def.ghi")
    expect(getAccessToken()).toBe("abc.def.ghi")
  })

  it("URL-encodes special characters in the token", () => {
    const token = "eyJhbGciOiJIUzI1NiJ9.+/="
    setAccessToken(token)
    expect(document.cookie).toContain(`${TOKEN_COOKIE}=${encodeURIComponent(token)}`)
    expect(getAccessToken()).toBe(token)
  })

  it("removes the token from the cookie on clear", () => {
    setAccessToken("abc")
    clearAccessToken()
    expect(getAccessToken()).toBeNull()
  })

  it("round-trips the refresh token through its own cookie", () => {
    setRefreshToken("refresh.jwt.123")
    expect(getRefreshToken()).toBe("refresh.jwt.123")
  })

  it("removes the refresh token from the cookie on clear", () => {
    setRefreshToken("abc")
    clearRefreshToken()
    expect(getRefreshToken()).toBeNull()
  })

  it("stores and clears the full session pair together", () => {
    setSession("access.one", "refresh.one")
    expect(getAccessToken()).toBe("access.one")
    expect(getRefreshToken()).toBe("refresh.one")
    clearSession()
    expect(getAccessToken()).toBeNull()
    expect(getRefreshToken()).toBeNull()
  })
})