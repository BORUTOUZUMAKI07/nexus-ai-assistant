import { describe, it, expect } from "vitest"
import { mergeAPIKey } from "@/lib/queries"
import type { APIKey } from "@/lib/api"

function key(over: Partial<APIKey>): APIKey {
  return {
    id: "key-1",
    user_id: "user-1",
    provider: "groq",
    key_preview: "gsk_    1234",
    label: "Groq",
    is_active: true,
    created_at: "2026-09-01T10:00:00Z",
    ...over,
  }
}

describe("mergeAPIKey", () => {
  it("replaces an existing entry that shares the created key's id", () => {
    // The backend upserts by (user, provider) and returns the *existing* row's
    // id. Appending would leave two entries behind one id: React warns about a
    // duplicate key and a single delete then removes both, which is exactly how
    // the live BYOK list fell from two rows to zero.
    const groq = key({ id: "key-1", provider: "groq", key_preview: "gsk_    1111" })
    const firecrawl = key({ id: "key-2", provider: "firecrawl", key_preview: "fc_    2222" })
    const updated = key({ id: "key-1", provider: "groq", key_preview: "gsk_    9999" })

    const merged = mergeAPIKey([groq, firecrawl], updated)

    expect(merged).toHaveLength(2)
    expect(merged.filter((k) => k.id === "key-1")).toHaveLength(1)
    // The refreshed row wins and the unrelated provider survives.
    expect(merged[0]).toBe(updated)
    expect(merged.find((k) => k.id === "key-2")).toBe(firecrawl)
  })

  it("prepends a genuinely new key", () => {
    const existing = key({ id: "key-1" })
    const created = key({ id: "key-3", provider: "openai" })

    const merged = mergeAPIKey([existing], created)

    expect(merged.map((k) => k.id)).toEqual(["key-3", "key-1"])
  })

  it("treats an undefined cache as empty", () => {
    const created = key({ id: "key-1" })
    expect(mergeAPIKey(undefined, created)).toEqual([created])
  })
})
