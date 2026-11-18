/**
 * The stream-level terminal frame: the one signal that says "this run really
 * finished" rather than "the socket happened to stop producing bytes".
 *
 * The parser must recognise it without disturbing the token/patch shape every
 * existing consumer already relies on: a terminal frame is metadata, not a
 * token, an annotation on the message, or an error.
 */
import { describe, it, expect } from "vitest"
import { consumeChatStream } from "@/lib/chatStream"

/** Build a data-stream body exactly as the BFF frames it. */
function streamOf(...frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame + "\n"))
      controller.close()
    },
  })
}

/** The additive terminal annotation, shaped exactly as the BFF emits it. */
function terminalFrame(fields: Record<string, unknown> = {}): string {
  return `8:[${JSON.stringify({ type: "done", ...fields })}]`
}

describe("consumeChatStream — terminal frame", () => {
  it("parses the terminal frame into result.terminal without changing tokens or patches", async () => {
    const patches: Array<{ content: string; annotations: unknown[] }> = []
    const result = await consumeChatStream(
      streamOf(
        '0:"hel"',
        '0:"lo"',
        terminalFrame({
          run_id: "run-1",
          message_id: "msg-1",
          thread_id: "thread-1",
          final_text_marker: "marker-1",
        })
      ),
      {
        patch: (content, annotations) => patches.push({ content, annotations }),
      }
    )

    expect(result.content).toBe("hello")
    expect(result.error).toBeNull()
    expect(result.terminal).toEqual({
      type: "done",
      run_id: "run-1",
      message_id: "msg-1",
      thread_id: "thread-1",
      final_text_marker: "marker-1",
    })
    // The terminal frame is not answer text and not a persisted annotation.
    // Widen to `unknown[]` first: the static union deliberately has no `done`
    // member, and this assertion is precisely about what appears at runtime.
    const annotationsAsData: unknown[] = result.annotations;
    expect(
      annotationsAsData.some(
        (a) => (a as { type?: unknown }).type === "done"
      )
    ).toBe(false);
    expect(patches[patches.length - 1].content).toBe("hello")
  })

  it("leaves terminal null for a stream that ended without one", async () => {
    // Older BFFs and interrupted sockets produce this shape. The field must be
    // explicitly null rather than absent, so a consumer can branch on it.
    const result = await consumeChatStream(streamOf('0:"partial"'), {
      patch: () => {},
    })

    expect(result.terminal).toBeNull()
    expect(result.content).toBe("partial")
  })

  it("does not invent a final_text_marker when the frame omits one", async () => {
    // Identity must never be fabricated from the answer text: the backend owns
    // the marker, or there isn't one.
    const result = await consumeChatStream(
      streamOf('0:"answer"', terminalFrame({ run_id: "run-2" })),
      { patch: () => {} }
    )

    expect(result.terminal).toEqual({ type: "done", run_id: "run-2" })
    expect(result.terminal?.final_text_marker).toBeUndefined()
  })
})
