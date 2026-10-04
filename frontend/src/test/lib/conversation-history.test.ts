/**
 * Turning stored message rows back into what the chat view renders.
 *
 * This mapper was extracted from the page's inline version, which had two
 * defects that no test could see because the columns it read were always empty:
 * it read `tool_calls[].name` while the streaming path writes `tool_calls[]
 * .tool_name`, and it set a citation's `source` from its `filename`. Both are
 * asserted here against the exact shape the backend writes.
 */
import { describe, it, expect } from "vitest"
import { hasAnswer, messagesFromHistory } from "@/lib/conversationHistory"
import type { ConversationMessage } from "@/lib/api"
import type {
  CitationAnnotation,
  NexusMessage,
  ReasoningAnnotation,
  ToolCallAnnotation,
} from "@/hooks/useNexusChat"

function row(over: Partial<ConversationMessage> = {}): ConversationMessage {
  return {
    id: "m1",
    role: "assistant",
    content: "answer",
    thought_process: null,
    model: "fast_chat",
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    citations: [],
    tool_calls: [],
    user_feedback: null,
    created_at: "2026-10-04T00:00:00Z",
    ...over,
  }
}

function annotations(m: NexusMessage, type: string) {
  return (m.annotations ?? []).filter((a) => a.type === type)
}

/**
 * The payload of one annotation, narrowed to the type it claims to be.
 *
 * `annotations(m, "citation")` returns the *union* of every annotation the mapper
 * can emit, so `.data` is `CitationData | ToolCallData | ReasoningData | ...` and
 * every property access on it is a type error — including `source`, which exists
 * on three of the members. Filtering by `a.type` is what narrows, and the filter
 * takes a `string`, so the narrowing has to be reasserted here. Without it these
 * tests do not compile, which is the type system doing the job the assertions
 * claim to: they read a field the annotation type says is optional.
 */
function dataOf<A extends { type: string; data: unknown }>(
  m: NexusMessage,
  type: A["type"]
): A["data"] {
  const found = (m.annotations ?? []).find((a) => a.type === type)
  if (!found) throw new Error(`no ${type} annotation on a message built with one`)
  return found.data as A["data"]
}

describe("messagesFromHistory", () => {
  it("keeps the backend's order rather than imposing its own", () => {
    const out = messagesFromHistory([
      row({ id: "u1", role: "user", content: "q" }),
      row({ id: "a1", role: "assistant", content: "a" }),
      row({ id: "u2", role: "user", content: "q2" }),
    ])
    expect(out.map((m) => m.id)).toEqual(["u1", "a1", "u2"])
  })

  it("returns nothing for absent input rather than throwing", () => {
    // The endpoint can answer without a `messages` key, and a reload must not
    // become a red screen because of it.
    expect(messagesFromHistory(null)).toEqual([])
    expect(messagesFromHistory(undefined)).toEqual([])
    expect(messagesFromHistory([])).toEqual([])
  })

  it("drops a row with no content", () => {
    // What an interrupted or content-filtered turn leaves behind. Rendering it
    // puts an empty box in the transcript where the user expects their answer.
    const out = messagesFromHistory([
      row({ id: "u1", role: "user", content: "q" }),
      row({ id: "blank", role: "assistant", content: "" }),
      row({ id: "a1", role: "assistant", content: "a" }),
    ])
    expect(out.map((m) => m.id)).toEqual(["u1", "a1"])
  })

  it("omits annotations entirely when there are none", () => {
    // Not cosmetic: several components distinguish an absent list from an empty
    // one, and most history messages have no annotations at all.
    const [m] = messagesFromHistory([row()])
    expect(m.annotations).toBeUndefined()
  })

  it("reads the tool-call shape the run executor actually writes", () => {
    // `tool_name`/`tool_input` are what `run_executor` collects from the frame
    // stream. The previous mapper read `name`/`args`, so every tool call stored
    // by the streaming path rendered with no name at all.
    const [m] = messagesFromHistory([
      row({
        tool_calls: [
          { tool_name: "web_search", tool_input: { q: "rag" }, tool_call_id: "tc-1" },
        ],
      }),
    ])
    const [call] = annotations(m, "tool_call") as ToolCallAnnotation[]
    expect(call.data.tool_name).toBe("web_search")
    expect(call.data.tool_input).toEqual({ q: "rag" })
    expect(call.data.tool_call_id).toBe("tc-1")
  })

  it("still reads the older name/args spelling", () => {
    // A stored row is whatever the code was writing when it was written, so a
    // row from before the rename has to keep rendering.
    const [m] = messagesFromHistory([
      row({ tool_calls: [{ name: "old_tool", args: { a: 1 }, tool_call_id: "tc-2" }] }),
    ])
    const [call] = annotations(m, "tool_call") as ToolCallAnnotation[]
    expect(call.data.tool_name).toBe("old_tool")
    expect(call.data.tool_input).toEqual({ a: 1 })
  })

  it("drops an unnamed tool call instead of rendering an empty card", () => {
    // The card's entire content is its name; one without a name is a bug in the
    // producer and shows up as a blank box in the transcript.
    const [m] = messagesFromHistory([
      row({ tool_calls: [{ tool_input: {}, tool_call_id: "tc-3" }] }),
    ])
    expect(annotations(m, "tool_call")).toEqual([])
  })

  it("gives a tool call an id when the row carries none", () => {
    // Keyed on the call so two nameless-input calls do not collapse into one
    // entry in the UI.
    const [m] = messagesFromHistory([
      row({
        tool_calls: [
          { tool_name: "a", tool_input: {} },
          { tool_name: "b", tool_input: {} },
        ],
      }),
    ])
    const calls = annotations(m, "tool_call") as ToolCallAnnotation[]
    expect(calls).toHaveLength(2)
    expect(calls[0].data.tool_call_id).not.toBe(calls[1].data.tool_call_id)
  })

  it("keeps a citation's own source distinct from its filename", () => {
    // A web citation has a `source` and no filename; a RAG citation the reverse.
    // Conflating them put both under the wrong label and rendered one blank.
    const [rag] = messagesFromHistory([
      row({ citations: [{ filename: "notes.pdf", chunk_index: 3, score: 0.8, content_snippet: "s" }] }),
    ])
    const ragCitation = dataOf<CitationAnnotation>(rag, "citation")
    expect(ragCitation.filename).toBe("notes.pdf")
    expect(ragCitation.source).toBe("notes.pdf")
    expect(ragCitation.snippet).toBe("s")

    const [web] = messagesFromHistory([
      row({ citations: [{ source: "example.com", snippet: "t" }] }),
    ])
    const webCitation = dataOf<CitationAnnotation>(web, "citation")
    expect(webCitation.source).toBe("example.com")
    expect(webCitation.snippet).toBe("t")
  })

  it("restores a stored thought process as a reasoning block", () => {
    const [withTrace] = messagesFromHistory([row({ thought_process: "I looked it up" })])
    expect(dataOf<ReasoningAnnotation>(withTrace, "reasoning").content).toBe("I looked it up")

    // And does not invent one: a row with no stored trace simply has no
    // reasoning, which is honest, rather than implying the model never reasoned.
    const [without] = messagesFromHistory([row({ thought_process: null })])
    expect(annotations(without, "reasoning")).toEqual([])
  })
})

describe("hasAnswer", () => {
  const rendered: NexusMessage[] = [{ id: "a1", role: "assistant", content: "x" }]

  it("matches on identity", () => {
    expect(hasAnswer(rendered, "a1")).toBe(true)
  })

  it("is false for a message the transcript does not hold", () => {
    // The positive counterpart: the same header must not suppress a replay whose
    // answer this view has never seen, which is the whole recovery feature.
    expect(hasAnswer(rendered, "a2")).toBe(false)
  })

  it("is false without a persisted id", () => {
    // A run still producing has no row yet, and "no id" must never mean "already
    // rendered" -- that reading silently swallows the answer of every live run.
    expect(hasAnswer(rendered, null)).toBe(false)
    expect(hasAnswer(rendered, undefined)).toBe(false)
    expect(hasAnswer(rendered, "")).toBe(false)
  })

  it("is false without a persisted id even when the transcript's own ids are unreadable", () => {
    // The guard's only *observable* case, and the reason it is not the redundant
    // check it first looks like.
    //
    // `rendered` carries real ids, so `messages.some(m => m.id === undefined)` is
    // false and the guard is invisible — a test written against that fixture
    // passes with the guard deleted, which is how a defensive check becomes a
    // claim nothing verifies (AGENTS.md §9.21).
    //
    // The transcript here comes from a history payload that lost its ids, which
    // `toMessage` copies through verbatim rather than substituting. Then "the run
    // reported no id" and "a row on screen has no id" both mean "unknown", and the
    // only safe reading of that pair is *render the replay*: suppressing it loses a
    // real answer with nothing on screen to replace it, which is the one failure
    // a recovery feature must not have.
    const unreadable = messagesFromHistory([{ ...row(), id: undefined as never }])
    expect(unreadable.map((m) => m.id)).toEqual([undefined])
    expect(hasAnswer(unreadable, undefined)).toBe(false)
    expect(hasAnswer(unreadable, null)).toBe(false)
    expect(hasAnswer(unreadable, "")).toBe(false)
    // The fixture check, which is the part that makes those three mean anything
    // (AGENTS.md §9.13): the predicate *is* reachable on this transcript — it is
    // the guard that stops it. Written as the raw predicate rather than as
    // `hasAnswer`, because no input can make `hasAnswer` return true here and an
    // assertion that can never hold is not a fixture check.
    expect(unreadable.some((m) => m.id === undefined)).toBe(true)
  })
})