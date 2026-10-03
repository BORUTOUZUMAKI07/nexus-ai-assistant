import { describe, it, expect, beforeEach, vi } from "vitest"
import { renderHook, act, waitFor } from "@/test/test-utils"
import { useNexusChat } from "@/hooks/useNexusChat"
import { server } from "@/test/mocks/server"
import { chatStreamBody } from "@/test/mocks/handlers"
import { http, HttpResponse } from "msw"

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

  it("streams text deltas and surfaces them on the assistant message", async () => {
    const { result } = renderHook(() => useNexusChat())

    await act(async () => {
      await result.current.sendMessage("Hello!")
    })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    const assistant = result.current.messages.find((m) => m.role === "assistant")
    expect(assistant?.content).toBe("Hello from Nexus.")
    expect(result.current.messages[0].role).toBe("user")
    expect(result.current.messages[0].content).toBe("Hello!")
  })

  it("concatenates multiple text deltas in order", async () => {
    // Every existing fixture sends a single `0:` frame, so nothing covered the
    // case that actually happens in production: a real answer arrives as dozens
    // of deltas. If the reader replaced content instead of appending, a
    // single-delta test would still pass and the user would see only the last
    // token of every sentence.
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          chatStreamBody(["Hello", ", ", "this ", "is ", "Nexus."]),
          { headers: { "Content-Type": "text/plain; charset=utf-8" } }
        )
      )
    )
    const { result } = renderHook(() => useNexusChat())

    await act(async () => {
      await result.current.sendMessage("Hello!")
    })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    const assistant = result.current.messages.find((m) => m.role === "assistant")
    expect(assistant?.content).toBe("Hello, this is Nexus.")
  })

  it("reassembles a frame split across two network chunks", async () => {
    // The important one. `reader.read()` returns whatever bytes happened to
    // arrive, which is *not* aligned to the SSE frame boundaries — a frame can
    // and does straddle a chunk. The reader has to hold the incomplete tail in a
    // buffer and prepend it to the next chunk, otherwise the split frame is
    // parsed as two malformed lines and that text silently vanishes from the
    // answer. Every pre-existing fixture delivered the whole body in one chunk,
    // so this path was entirely untested.
    const encoder = new TextEncoder()
    server.use(
      http.post("/api/chat", () => {
        const stream = new ReadableStream({
          start(controller) {
            // Deliberately cut mid-frame and mid-token.
            controller.enqueue(encoder.encode('0:"Hello from N'))
            controller.enqueue(encoder.encode('exus."\n'))
            controller.close()
          },
        })
        return new HttpResponse(stream, {
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        })
      })
    )
    const { result } = renderHook(() => useNexusChat())

    await act(async () => {
      await result.current.sendMessage("Hello!")
    })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    const assistant = result.current.messages.find((m) => m.role === "assistant")
    expect(assistant?.content).toBe("Hello from Nexus.")
  })

  it("keeps a trailing frame that arrives without a final newline", async () => {
    // The reader pops the last element of the split as its buffer and processes
    // it after the loop ends. A backend that closes the stream without the
    // trailing newline — or a proxy that trims it — must not lose that last
    // delta, which is typically the end of the answer.
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse('0:"Complete answer"', {
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        })
      )
    )
    const { result } = renderHook(() => useNexusChat())

    await act(async () => {
      await result.current.sendMessage("Hello!")
    })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    const assistant = result.current.messages.find((m) => m.role === "assistant")
    expect(assistant?.content).toBe("Complete answer")
  })

  it("surfaces hitl_request annotations as pendingHITL", async () => {
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          chatStreamBody(["Requesting approval"], [
            {
              type: "hitl_request",
              data: {
                thread_id: "thread-9",
                request: "Approve deploying the change?",
                plan: ["Deploy", "Verify"],
              },
            },
          ]),
          { headers: { "Content-Type": "text/plain; charset=utf-8" } }
        )
      )
    )
    const { result } = renderHook(() => useNexusChat())

    await act(async () => {
      await result.current.sendMessage("Deploy it")
    })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.pendingHITL?.thread_id).toBe("thread-9")
  })

  it("reports a generated artifact through onArtifactSaved", async () => {
    // The event that makes the canvas open by itself. Without it a document
    // lands in the database and the user is never told, which is the same
    // invisible state the artifact list had before the backend re-fetched it.
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          chatStreamBody(["Here is the report."], [
            {
              type: "artifact",
              data: {
                artifact_id: "art-77",
                title: "Q3 Report",
                version: 1,
                created: true,
              },
            },
          ]),
          { headers: { "Content-Type": "text/plain; charset=utf-8" } }
        )
      )
    )
    const onArtifactSaved = vi.fn()
    const { result } = renderHook(() => useNexusChat({ onArtifactSaved }))

    await act(async () => {
      await result.current.sendMessage("write me a report on Q3")
    })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(onArtifactSaved).toHaveBeenCalledTimes(1)
    expect(onArtifactSaved).toHaveBeenCalledWith({
      artifact_id: "art-77",
      title: "Q3 Report",
      version: 1,
      created: true,
    })
  })

  it("does not fire onArtifactSaved on a plain chat turn", async () => {
    // The overwhelmingly common case. A callback that fired on every turn would
    // train the page to open an empty canvas constantly, so silence here is the
    // property that matters, not the happy path.
    const onArtifactSaved = vi.fn()
    const { result } = renderHook(() => useNexusChat({ onArtifactSaved }))

    await act(async () => {
      await result.current.sendMessage("Hello!")
    })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(onArtifactSaved).not.toHaveBeenCalled()
  })

  it("keeps the artifact out of message annotations", async () => {
    // The annotation array is rebuilt from reasoning/citations/tool-calls on
    // every patch, so an artifact entry placed there would be erased by the
    // next text delta. It is a callback-only signal, and this pins that.
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          chatStreamBody(["Report body."], [
            {
              type: "artifact",
              data: {
                artifact_id: "art-1",
                title: "T",
                version: 1,
                created: true,
              },
            },
          ]),
          { headers: { "Content-Type": "text/plain; charset=utf-8" } }
        )
      )
    )
    const { result } = renderHook(() => useNexusChat())

    await act(async () => {
      await result.current.sendMessage("write a report")
    })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    const assistant = result.current.messages.find((m) => m.role === "assistant")
    expect(assistant?.annotations ?? []).toEqual([])
  })

  it("surfaces the critic verdict and evidence score as turn state", async () => {
    // These two frames used to be translated by nothing and read by nothing.
    // The route-level test pins that they now reach the client; this pins that
    // the client does something with them. Either half alone would pass while
    // the user still never sees the verdict.
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          chatStreamBody(["Here is the answer."], [
            { type: "critique", data: { critique: "too thin", revision_count: 2 } },
            { type: "quality", data: { evidence_score: 0.42, evidence_gate_passed: false } },
          ]),
          { headers: { "Content-Type": "text/plain; charset=utf-8" } }
        )
      )
    )
    const { result } = renderHook(() => useNexusChat())

    await act(async () => {
      await result.current.sendMessage("summarise the architecture")
    })

    await waitFor(() => expect(result.current.turnVerdict).not.toBeNull())
    expect(result.current.turnVerdict).toEqual({
      critique: "too thin",
      revision_count: 2,
      evidence_score: 0.42,
      evidence_gate_passed: false,
    })
  })

  it("keeps both halves when the quality frame arrives without a critique", async () => {
    // The two frames are independent. State written per frame would let the
    // second clobber the first, losing the score on every turn that was
    // accepted without revision -- which is the common case.
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          chatStreamBody(["Short answer."], [
            { type: "quality", data: { evidence_score: 0.88, evidence_gate_passed: true } },
          ]),
          { headers: { "Content-Type": "text/plain; charset=utf-8" } }
        )
      )
    )
    const { result } = renderHook(() => useNexusChat())

    await act(async () => {
      await result.current.sendMessage("hi")
    })

    await waitFor(() => expect(result.current.turnVerdict).not.toBeNull())
    expect(result.current.turnVerdict?.evidence_score).toBe(0.88)
    expect(result.current.turnVerdict?.critique).toBeUndefined()
    expect(result.current.turnVerdict?.revision_count).toBe(0)
  })

  it("reports an unreported evidence gate as null, not as a pass", async () => {
    // `?? true` here would turn the backend's silence into a clean bill of
    // health -- the one default that manufactures a claim nobody made.
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          chatStreamBody(["Answer."], [
            { type: "quality", data: { evidence_score: 0.5 } },
          ]),
          { headers: { "Content-Type": "text/plain; charset=utf-8" } }
        )
      )
    )
    const { result } = renderHook(() => useNexusChat())

    await act(async () => {
      await result.current.sendMessage("hi")
    })

    await waitFor(() => expect(result.current.turnVerdict).not.toBeNull())
    expect(result.current.turnVerdict?.evidence_gate_passed).toBeNull()
  })

  it("never puts the critic's words in the assistant's answer", async () => {
    // The regression this guards is worse than the original omission: a `0:`
    // frame would render "too thin" as though the assistant had said it.
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          chatStreamBody(["Here is the answer."], [
            { type: "critique", data: { critique: "too thin", revision_count: 1 } },
          ]),
          { headers: { "Content-Type": "text/plain; charset=utf-8" } }
        )
      )
    )
    const { result } = renderHook(() => useNexusChat())

    await act(async () => {
      await result.current.sendMessage("hi")
    })

    await waitFor(() => expect(result.current.turnVerdict).not.toBeNull())
    const assistant = result.current.messages.find((m) => m.role === "assistant")
    expect(assistant?.content).toBe("Here is the answer.")
    expect(assistant?.content).not.toContain("too thin")
  })

  it("clears the verdict at the start of the next turn", async () => {
    // Otherwise run N's verdict is displayed against run N+1's answer, which is
    // a claim about the wrong turn rather than a merely stale one.
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          chatStreamBody(["Answer."], [
            { type: "quality", data: { evidence_score: 0.3, evidence_gate_passed: false } },
          ]),
          { headers: { "Content-Type": "text/plain; charset=utf-8" } }
        )
      )
    )
    const { result } = renderHook(() => useNexusChat())

    await act(async () => {
      await result.current.sendMessage("first")
    })
    await waitFor(() => expect(result.current.turnVerdict).not.toBeNull())

    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          chatStreamBody(["Second answer, no verdict frames."]),
          { headers: { "Content-Type": "text/plain; charset=utf-8" } }
        )
      )
    )
    await act(async () => {
      await result.current.sendMessage("second")
    })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.turnVerdict).toBeNull()
  })

  it("records an error state when the chat endpoint fails", async () => {
    server.use(
      http.post("/api/chat", () =>
        HttpResponse.json({ detail: "boom" }, { status: 503 })
      )
    )
    const { result } = renderHook(() => useNexusChat())

    await act(async () => {
      await result.current.sendMessage("Hello!")
    })

    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(result.current.isLoading).toBe(false)
  })

  it("stop aborts an in-flight request and clears the loading flag", () => {
    const { result } = renderHook(() => useNexusChat())
    act(() => result.current.stop())
    expect(result.current.isLoading).toBe(false)
  })

  // ── Queue (C1) ────────────────────────────────────────────────────────────
  // A /api/chat whose body stays open until the test releases it, so "a send
  // while a run is in flight" is deterministic rather than a race against a
  // fast mock. `frames` is the raw data-stream body.
  function gatedChat(frames = '0:"answer"\n') {
    const encoder = new TextEncoder()
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          new ReadableStream({
            start(controller) {
              controller.enqueue(encoder.encode(frames))
              void gate.then(() => {
                try {
                  controller.close()
                } catch {
                  // The stream was cancelled (an aborted run). Closing a
                  // cancelled controller throws and would surface as an
                  // unhandled rejection in the test process.
                }
              })
            },
          }),
          { headers: { "Content-Type": "text/plain; charset=utf-8" } }
        )
      )
    )
    return release
  }

  const userContents = (result: { current: { messages: { role: string; content: string }[] } }) =>
    result.current.messages
      .filter((m) => m.role === "user")
      .map((m) => m.content)

  it("queues a follow-up submitted while a run is in flight instead of dropping it", async () => {
    const release = gatedChat()
    const { result } = renderHook(() => useNexusChat())

    let first!: Promise<void>
    act(() => {
      first = result.current.sendMessage("first")
    })

    await act(async () => {
      await result.current.sendMessage("second")
    })

    // The second send is held, not dispatched and not discarded.
    expect(result.current.queuedMessages.map((q) => q.content)).toEqual(["second"])
    expect(userContents(result)).toEqual(["first"])

    await act(async () => {
      release()
      await first
    })
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.queuedMessages).toHaveLength(0)
    expect(userContents(result)).toEqual(["first", "second"])
  })

  it("drains queued turns in submission order", async () => {
    const release = gatedChat()
    const { result } = renderHook(() => useNexusChat())

    let first!: Promise<void>
    act(() => {
      first = result.current.sendMessage("first")
    })
    await act(async () => {
      await result.current.sendMessage("second")
      await result.current.sendMessage("third")
    })
    expect(result.current.queuedMessages.map((q) => q.content)).toEqual([
      "second",
      "third",
    ])

    await act(async () => {
      release()
      await first
    })
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(userContents(result)).toEqual(["first", "second", "third"])
  })

  it("cancelQueued drops a queued turn before it starts", async () => {
    const release = gatedChat()
    const { result } = renderHook(() => useNexusChat())

    let first!: Promise<void>
    act(() => {
      first = result.current.sendMessage("first")
    })
    await act(async () => {
      await result.current.sendMessage("second")
    })
    const queuedId = result.current.queuedMessages[0].id
    act(() => {
      result.current.cancelQueued(queuedId)
    })
    expect(result.current.queuedMessages).toHaveLength(0)

    await act(async () => {
      release()
      await first
    })
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(userContents(result)).toEqual(["first"])
  })

  it("holds the queue while a human approval is pending, then drains on resolve", async () => {
    // An interrupted run ends the stream, so the queue must not advance into a
    // second run while the approval is unanswered -- and it must advance once
    // the approval is recorded, or the queued turn is stranded forever.
    const release = gatedChat(
      '0:"need approval"\n' +
        '8:[{"type":"hitl_request","data":{"thread_id":"t1","request":"Run tool?"}}]\n'
    )
    const { result } = renderHook(() => useNexusChat())

    let first!: Promise<void>
    act(() => {
      first = result.current.sendMessage("first")
    })
    await act(async () => {
      await result.current.sendMessage("second")
    })

    await act(async () => {
      release()
      await first
    })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    expect(result.current.pendingHITL).not.toBeNull()
    // Held: the approval owns the conversation.
    expect(result.current.queuedMessages.map((q) => q.content)).toEqual(["second"])
    expect(userContents(result)).toEqual(["first"])

    await act(async () => {
      await result.current.resolveHITL("approve")
    })
    await waitFor(() => expect(userContents(result)).toEqual(["first", "second"]))
  })

  it("clearMessages drops queued turns along with the transcript", async () => {
    const release = gatedChat()
    const { result } = renderHook(() => useNexusChat())

    let first!: Promise<void>
    act(() => {
      first = result.current.sendMessage("first")
    })
    await act(async () => {
      await result.current.sendMessage("second")
    })
    expect(result.current.queuedMessages).toHaveLength(1)

    act(() => {
      result.current.clearMessages()
    })
    expect(result.current.queuedMessages).toHaveLength(0)

    // The queued turn was dropped with the transcript: releasing the run must
    // not fire it into the now-empty conversation.
    await act(async () => {
      release()
      await first
    })
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(userContents(result)).toEqual([])
  })

  it("stop leaves the slot held until the aborted run settles", async () => {
    // `stop()` used to clear `loadingRef`/`isLoading` synchronously. The aborted
    // run's own `finally` then cleared the flag again while the follow-up run
    // was already in flight, so a further send could start a third concurrent
    // run. Abort-only means the flag is released by that `finally`, not here.
    const release = gatedChat()
    const { result } = renderHook(() => useNexusChat())

    let first!: Promise<void>
    act(() => {
      first = result.current.sendMessage("first")
    })
    expect(result.current.isLoading).toBe(true)

    act(() => {
      result.current.stop()
    })
    expect(result.current.isLoading).toBe(true)

    await act(async () => {
      release()
      await first.catch(() => {})
    })
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(userContents(result)).toEqual(["first"])
  })

  it("sends the drained turn with the previous answer in its history", async () => {
    // The final text delta is a trailing frame with no newline, so it is only
    // applied by the post-loop flush — *after* the last `await`. The drain then
    // starts the next turn in the same tick, so the history it reads must
    // already contain that delta. With a render-scheduled messages ref it would
    // not, and the follow-up would be sent without the tail of the answer it is
    // replying to. This is what makes the synchronous ref update load-bearing.
    const bodies: { messages: { role: string; content: unknown }[] }[] = []
    const encoder = new TextEncoder()
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.post("/api/chat", async ({ request }) => {
        bodies.push((await request.json()) as never)
        return new HttpResponse(
          new ReadableStream({
            start(controller) {
              controller.enqueue(encoder.encode('0:"answer "\n0:"one"'))
              void gate.then(() => controller.close())
            },
          }),
          { headers: { "Content-Type": "text/plain; charset=utf-8" } }
        )
      })
    )
    const { result } = renderHook(() => useNexusChat())

    let first!: Promise<void>
    act(() => {
      first = result.current.sendMessage("first")
    })
    await act(async () => {
      await result.current.sendMessage("second")
    })
    await act(async () => {
      release()
      await first
    })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    expect(bodies).toHaveLength(2)
    expect(bodies[1].messages).toEqual([
      { role: "user", content: "first" },
      { role: "assistant", content: "answer one" },
      { role: "user", content: "second" },
    ])
  })
})