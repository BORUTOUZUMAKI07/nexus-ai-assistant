/**
 * The browser half of run completion.
 *
 * A stream that stops without an explicit terminal frame used to leave the
 * spinner, the stage label and the Stop button stuck forever: `isLoading` was
 * cleared only on a clean EOF in `sendMessage`'s `finally`. These tests pin the
 * three recovery behaviours that replace that assumption:
 *
 *   - any interrupted turn releases the loading/stage state unconditionally;
 *   - a silent stream is bounded by an inactivity timeout with a clear error;
 *   - a clean close without a terminal frame triggers bounded rejoin polling
 *     that observes the completion without the user doing anything.
 */
import { describe, it, expect, beforeEach } from "vitest"
import { renderHook, act, waitFor } from "@/test/test-utils"
import { useNexusChat } from "@/hooks/useNexusChat"
import { server } from "@/test/mocks/server"
import { http, HttpResponse } from "msw"

const CONV = "22222222-2222-4222-8222-222222222222"
const RUN = "11111111-1111-4111-8111-111111111111"
const MSG = "33333333-3333-4333-8333-333333333333"
const STORAGE_KEY = `nexus-active-run:${CONV}`

const encoder = new TextEncoder()

/** The terminal annotation a completed BFF stream ends with. */
function terminalFrame(fields: Record<string, unknown> = {}): string {
  return `8:[${JSON.stringify({ type: "done", run_id: RUN, ...fields })}]\n`
}

/** A status annotation, as the BFF frames a backend progress event. */
function statusFrame(label: string): string {
  return `8:${JSON.stringify([
    { type: "status", data: { stage: "synthesizer", label } },
  ])}\n`
}

describe("chat terminal state in useNexusChat", () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it("clears the spinner and the stage label when a streaming turn is interrupted", async () => {
    // S2/S3: the old code cleared `isLoading` only on a clean stream EOF, and
    // never cleared `statusLabel` at all. Stop mid-stream left "Writing the
    // answer" and a live Stop button on screen for the rest of the session.
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(encoder.encode(statusFrame("Writing the answer")))
              void gate.then(() => {
                try {
                  controller.close()
                } catch {
                  // The stream was cancelled by the abort; closing a cancelled
                  // controller throws and would become an unhandled rejection.
                }
              })
            },
          }),
          { headers: { "Content-Type": "text/plain; charset=utf-8" } }
        )
      )
    )

    const { result } = renderHook(() => useNexusChat())
    let send!: Promise<void>
    act(() => {
      send = result.current.sendMessage("hello")
    })
    await waitFor(() => expect(result.current.statusLabel).toBe("Writing the answer"))
    expect(result.current.isLoading).toBe(true)

    act(() => result.current.stop())
    await act(async () => {
      release()
      await send.catch(() => {})
    })

    expect(result.current.isLoading).toBe(false)
    expect(result.current.statusLabel).toBeNull()
  })

  it("bounds a silent stream with an inactivity timeout and a clear error", async () => {
    // A backend that accepts the request and then never writes a byte used to
    // hold the loading flag forever. The timer must fire from stream open (not
    // only after the first byte), cancel the reader, and surface an error that
    // says what happened.
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(new ReadableStream<Uint8Array>({ start() {} }), {
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        })
      )
    )

    const { result } = renderHook(() => useNexusChat({ streamInactivityMs: 40 }))
    await act(async () => {
      await result.current.sendMessage("hello")
    })

    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(result.current.error?.message).toMatch(/stall|no data|timed out/i)
    expect(result.current.isLoading).toBe(false)
    expect(result.current.statusLabel).toBeNull()
  })

  it("polls the rejoin GET after a close without a terminal frame and observes completion", async () => {
    // S4: a stream that ends cleanly but has no terminal frame is ambiguous --
    // the run may still be going server-side. The existing rejoin GET is the
    // completion oracle; bounded polling of it must retire the stored run id
    // and fill in the final answer with no user action.
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse('0:"partial"\n', {
          headers: {
            "Content-Type": "text/plain; charset=utf-8",
            "X-Nexus-Run-Id": RUN,
          },
        })
      ),
      http.get("/api/conversations/:id", () =>
        HttpResponse.json({ id: CONV, title: "t", system_prompt: null, messages: [] })
      )
    )

    let gets = 0
    server.use(
      http.get("/api/chat", () => {
        gets += 1
        if (gets < 2) {
          // First poll: run still in flight, replay has no terminal frame.
          return new HttpResponse('0:"partial"\n', {
            headers: { "Content-Type": "text/plain; charset=utf-8" },
          })
        }
        return new HttpResponse(`0:"final answer"\n${terminalFrame({ message_id: MSG, thread_id: CONV })}`, {
          headers: {
            "Content-Type": "text/plain; charset=utf-8",
            "X-Nexus-Run-Id": RUN,
            "X-Nexus-Message-Id": MSG,
          },
        })
      })
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await act(async () => {
      await result.current.sendMessage("hello")
    })

    // The run id is kept after the ambiguous close, then retired when a poll
    // finally sees the terminal frame.
    await waitFor(() => expect(gets).toBeGreaterThanOrEqual(2))
    await waitFor(() => expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull())
    expect(result.current.messages.find((m) => m.role === "assistant")?.content).toBe(
      "final answer"
    )
    expect(result.current.isLoading).toBe(false)
  })
})
