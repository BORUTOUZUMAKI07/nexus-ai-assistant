/**
 * The page rebuilds the assistant transcript's view models on every render.
 * During a stream that is once per painted frame, and the work was O(all turns)
 * each time: four annotation filters and five array allocations per message,
 * for a conversation that may hold dozens of turns. The hook's patcher already
 * keeps every untouched message object by reference, so a WeakMap keyed on that
 * object can reuse the derived view model for every turn except the one that
 * actually changed.
 */
import { describe, expect, it, vi } from "vitest"
import {
  createMessageItemCache,
  deriveMessageItems,
  type MessageAnnotationGetters,
} from "@/lib/message-items"
import type {
  CitationAnnotation,
  NexusAnnotation,
  NexusMessage,
  ReasoningAnnotation,
  ToolCallAnnotation,
} from "@/hooks/useNexusChat"

const getters: MessageAnnotationGetters = {
  getReasoningBlocks: (m) =>
    (m.annotations ?? []).flatMap((a) => (a.type === "reasoning" ? [a] : [])),
  getCitations: (m) =>
    (m.annotations ?? []).flatMap((a) => (a.type === "citation" ? [a] : [])),
  getToolCalls: (m) =>
    (m.annotations ?? []).flatMap((a) => (a.type === "tool_call" ? [a] : [])),
}

const userTurn: NexusMessage = {
  id: "u1",
  role: "user",
  content: "What is in my files?",
}

const assistantTurn: NexusMessage = {
  id: "a1",
  role: "assistant",
  content: "First draft.",
  cost_usd: 0.012,
  total_tokens: 42,
  annotations: [
    {
      type: "reasoning",
      data: { content: "Look at the files." },
    } satisfies ReasoningAnnotation,
    {
      type: "citation",
      data: {
        filename: "spec.pdf",
        score: 0.91,
        content_snippet: "The spec says…",
      },
    } satisfies CitationAnnotation,
    {
      type: "tool_call",
      data: {
        tool_name: "rag_search",
        tool_input: { query: "files" },
        tool_call_id: "tc1",
        status: "completed",
      },
    } satisfies ToolCallAnnotation,
  ] satisfies NexusAnnotation[],
}

describe("deriveMessageItems", () => {
  it("reuses the derived item when the message object is unchanged", () => {
    const cache = createMessageItemCache()

    const first = deriveMessageItems([userTurn, assistantTurn], getters, cache)
    const second = deriveMessageItems([userTurn, assistantTurn], getters, cache)

    expect(second).toHaveLength(2)
    expect(second[0]).toBe(first[0])
    expect(second[1]).toBe(first[1])
  })

  it("does not call the annotation getters for cached messages", () => {
    const cache = createMessageItemCache()
    deriveMessageItems([userTurn, assistantTurn], getters, cache)
    const spies = {
      getReasoningBlocks: vi.fn(getters.getReasoningBlocks),
      getCitations: vi.fn(getters.getCitations),
      getToolCalls: vi.fn(getters.getToolCalls),
    }

    deriveMessageItems([userTurn, assistantTurn], spies, cache)

    expect(spies.getReasoningBlocks).not.toHaveBeenCalled()
    expect(spies.getCitations).not.toHaveBeenCalled()
    expect(spies.getToolCalls).not.toHaveBeenCalled()
  })

  it("recomputes only the patched message and keeps its neighbours identical", () => {
    const cache = createMessageItemCache()
    const before = deriveMessageItems([userTurn, assistantTurn], getters, cache)

    // This is exactly what the hook's patchMessage does: a new object for the
    // live turn, the same object reference for every other turn.
    const patched: NexusMessage = {
      ...assistantTurn,
      content: "First draft. With more words.",
    }
    const after = deriveMessageItems([userTurn, patched], getters, cache)

    expect(after[0]).toBe(before[0])
    expect(after[1]).not.toBe(before[1])
    expect(after[1].content).toBe("First draft. With more words.")
  })

  it("derives thoughts, citations and tool calls exactly as the inline mapper did", () => {
    const item = deriveMessageItems([assistantTurn], getters, createMessageItemCache())[0]

    expect(item.thought_process).toBe("Look at the files.")
    expect(item.citations).toEqual([
      {
        filename: "spec.pdf",
        // The annotation type does not declare chunk_index; the mapper reads
        // it with a cast and defaults to 0 when it is absent.
        chunk_index: 0,
        score: 0.91,
        content_snippet: "The spec says…",
      },
    ])
    expect(item.tool_calls).toEqual([
      {
        name: "rag_search",
        args: { query: "files" },
        result: undefined,
        status: "completed",
      },
    ])
    expect(item.cost_usd).toBe(0.012)
    expect(item.total_tokens).toBe(42)
  })

  it("uses undefined rather than empty arrays for turns without annotations", () => {
    const item = deriveMessageItems([userTurn], getters, createMessageItemCache())[0]

    expect(item.citations).toBeUndefined()
    expect(item.tool_calls).toBeUndefined()
    expect(item.thought_process).toBeUndefined()
  })
})
