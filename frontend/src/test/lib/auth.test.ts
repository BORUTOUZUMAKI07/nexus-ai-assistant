import { describe, it, expect, beforeEach } from "vitest"
import {
  getAccessToken,
  setAccessToken,
  clearAccessToken,
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
})