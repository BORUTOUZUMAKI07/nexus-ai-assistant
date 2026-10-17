/**
 * The composer's toggles resolved to the single `mode` the backend receives.
 *
 * This mapping used to live inline in `app/app/page.tsx` with `fast` folded into
 * `normal`, and the backend read neither, so the Fast/Deep toggle changed
 * nothing. Moving it here makes the priority — Code > Web > Fast/Deep — a red
 * build rather than an unverified branch, and `fast` a value with a real meaning.
 */
import { describe, it, expect } from "vitest"
import { resolveConversationMode } from "@/lib/chatMode"

describe("resolveConversationMode", () => {
  it("sends fast mode as `fast`, not a cosmetic `normal`", () => {
    expect(resolveConversationMode({ agentMode: "fast" })).toBe("fast")
  })

  it("sends deep mode through the full agent graph", () => {
    expect(resolveConversationMode({ agentMode: "deep" })).toBe("agent")
  })

  it("defaults to the full graph when no toggle is set", () => {
    expect(resolveConversationMode({})).toBe("agent")
  })

  it("lets Code override the Fast/Deep toggle", () => {
    expect(resolveConversationMode({ enableCode: true, agentMode: "fast" })).toBe("code")
    expect(resolveConversationMode({ enableCode: true, agentMode: "deep" })).toBe("code")
  })

  it("lets Web override the Fast/Deep toggle", () => {
    expect(resolveConversationMode({ enableWeb: true, agentMode: "fast" })).toBe("research")
    expect(resolveConversationMode({ enableWeb: true, agentMode: "deep" })).toBe("research")
  })

  it("ranks Code above Web when both are on", () => {
    expect(resolveConversationMode({ enableCode: true, enableWeb: true })).toBe("code")
  })
})
