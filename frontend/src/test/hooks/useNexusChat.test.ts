import { describe, it, expect, beforeEach, vi } from "vitest"
import { renderHook, act, waitFor } from "@/test/test-utils"
import { useNexusChat } from "@/hooks/useNexusChat"

describe("useNexusChat", () => {
  const submitEvent = () => ({ preventDefault: vi.fn() }) as unknown as React.FormEvent

  beforeEach(() => {
    document.cookie.split(";").forEach((c) => {
      const name = c.split("=")[0].trim()
      document.cookie = `${name}=; path=/; max-age=0`
    })
  })

  it("starts with empty state", () => {
    const { result } = renderHook(() => useNexusChat())
    expect(result.current.messages).toEqual([])
    expect(result.current.input).toBe("")
    expect(result.current.isLoading).toBe(false)
    expect(result.current.pendingHITL).toBeNull()
    expect(result.current.error).toBeNull()
  })

  it("tracks the input value via handleInputChange", () => {
    const { result } = renderHook(() => useNexusChat())
    act(() => {
      result.current.handleInputChange({
        target: { value: "hello" },
      } as React.ChangeEvent<HTMLInputElement>)
    })
    expect(result.current.input).toBe("hello")
  })

  it("placeholder handleSubmit prevents default and does not crash", () => {
    const { result } = renderHook(() => useNexusChat())
    const event = submitEvent()
    act(() => {
      result.current.handleSubmit(event)
    })
    expect(event.preventDefault).toHaveBeenCalled()
  })

  it("filters tool_calls / citations / reasoning from annotations", () => {
    const { result } = renderHook(() => useNexusChat())
    const message = {
      id: "m1",
      role: "assistant" as const,
      content: "done",
      annotations: [
        { type: "tool_call" as const, data: { tool_name: "web_search", tool_input: {}, tool_call_id: "tc-1" } },
        { type: "citation" as const, data: { source: "docs", snippet: "s", score: 0.9 } },
        { type: "reasoning" as const, data: { content: "think" } },
      ],
    }
    expect(result.current.getToolCalls(message)).toHaveLength(1)
    expect(result.current.getToolCalls(message)[0].data.tool_name).toBe("web_search")
    expect(result.current.getCitations(message)).toHaveLength(1)
    expect(result.current.getReasoningBlocks(message)).toHaveLength(1)
  })

  it("resolveHITL is a safe no-op without a pending request", async () => {
    const { result } = renderHook(() => useNexusChat())
    await act(async () => {
      await result.current.resolveHITL("approve")
    })
    expect(result.current.pendingHITL).toBeNull()
  })

  it("extracts and annotates hitl_request annotations without crashing", () => {
    const { result } = renderHook(() => useNexusChat())
    act(() => {
      result.current.setMessages([
        {
          id: "m2",
          role: "assistant",
          content: "need approval",
          annotations: [
            {
              type: "hitl_request",
              data: { thread_id: "thread-9", request: "Approve the change?" },
            },
          ],
        },
      ])
    })
    const annotations = result.current.messages[0].annotations
    expect(annotations?.[0].type).toBe("hitl_request")
  })

  it("stop clears the loading flag", () => {
    const { result } = renderHook(() => useNexusChat())
    act(() => result.current.stop())
    expect(result.current.isLoading).toBe(false)
  })
})