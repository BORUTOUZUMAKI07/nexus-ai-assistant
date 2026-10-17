/**
 * The model catalogue shared by the picker, Settings and the landing page.
 *
 * The landing page used to keep its own hardcoded list, and it had drifted onto
 * a removed model (`groq/compound-mini`) and a slug that never existed
 * (`nvidia/nemotron-3:free`) — nothing failed, because a marketing string is
 * not type-checked against the router. The list now lives in one place and this
 * test pins it, so the drift is a red build instead of a wrong claim.
 */
import { describe, it, expect } from "vitest"
import { MODELS, ROUTING_TIERS, modelDisplayName } from "@/lib/models"

describe("model catalogue", () => {
  it("offers exactly the two selectable router groups", () => {
    expect(MODELS.map((m) => m.id).sort()).toEqual([
      "complex_reasoning",
      "fast_chat",
    ])
  })

  it("advertises real routing tiers, not removed or invented slugs", () => {
    const slugs = ROUTING_TIERS.map((t) => t.model)
    // The two values that shipped and were wrong:
    expect(slugs).not.toContain("groq/compound-mini")
    expect(slugs).not.toContain("nvidia/nemotron-3:free")
    expect(slugs).toEqual([
      "groq/qwen/qwen3.8-27b",
      "groq/openai/gpt-oss-120b",
      "gemini/gemini-3.5-flash",
    ])
  })

  it("gives every routing tier a provider, latency and use", () => {
    for (const tier of ROUTING_TIERS) {
      expect(tier.provider.length).toBeGreaterThan(0)
      expect(tier.latency.length).toBeGreaterThan(0)
      expect(tier.use.length).toBeGreaterThan(0)
    }
  })

  it("maps a selectable group id to its display name", () => {
    expect(modelDisplayName("fast_chat")).toContain("Qwen 3.8 27B")
  })

  it("labels the Gemini fallbacks instead of showing the raw slug", () => {
    // The backend reports `response.model`, which is the bare slug for Gemini
    // (§9.39: complex_reasoning/fast_chat resolve to `gemini-3.5-flash-lite`
    // while Groq is egress-blocked; measured live: litellm returns the bare
    // name, no `gemini/` prefix). Without these entries the transcript rendered
    // the raw model string.
    expect(modelDisplayName("gemini-3.5-flash-lite")).toBe(
      "Gemini 3.5 Flash Lite (fallback)",
    )
    expect(modelDisplayName("gemini-3.5-flash")).toBe(
      "Gemini 3.5 Flash (vision)",
    )
    // A provider-prefixed form (which litellm does not report here) still
    // resolves, via the bare-strip — pinning that the strip is not
    // Gemini-specific.
    expect(modelDisplayName("gemini/gemini-3.5-flash-lite")).toBe(
      "Gemini 3.5 Flash Lite (fallback)",
    )
  })

  it("falls back to the raw string for an unknown model", () => {
    expect(modelDisplayName("some/unknown-model")).toBe("some/unknown-model")
  })

  it("returns undefined for a missing model", () => {
    expect(modelDisplayName(undefined)).toBeUndefined()
  })
})
