/**
 * The progress half of `consumeChatStream`: `status` annotations.
 *
 * The synthesizer streams only its finished, guardrailed text, so an
 * agent-mode run emits nothing for most of a minute — measured live, first
 * frame at 53 s of an 80 s run. The backend now announces each node start, and
 * those frames are the only thing standing between "Thinking…" and a screen
 * that looks hung. Three properties matter, and each fails silently:
 *
 *   - the label reaches the sink, or the progress line never changes;
 *   - the label never becomes answer text, or the stage name is persisted into
 *     the reply the user copies;
 *   - a frame without a label is ignored, or the progress line blanks.
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

function statusFrame(label?: string, stage = "synthesizer"): string {
  const data = label === undefined ? { stage } : { stage, label }
  return `8:${JSON.stringify([{ type: "status", data }])}`
}

describe("consumeChatStream — status frames", () => {
  it("reports every stage label to the sink, in order", async () => {
    const labels: string[] = []
    await consumeChatStream(
      streamOf(
        statusFrame("Checking evidence", "critic_grader"),
        statusFrame("Writing the answer")
      ),
      { patch: () => {}, onStatus: (label) => labels.push(label) }
    )

    expect(labels).toEqual(["Checking evidence", "Writing the answer"])
  })

  it("never renders a stage label as answer text", async () => {
    const patches: string[] = []
    const result = await consumeChatStream(
      streamOf(statusFrame("Writing the answer"), '0:"done"'),
      { patch: (content) => patches.push(content) }
    )

    expect(result.content).toBe("done")
    expect(patches.every((p) => !p.includes("Writing the answer"))).toBe(true)
  })

  it("ignores a status frame with no label instead of blanking the line", async () => {
    const labels: string[] = []
    await consumeChatStream(streamOf(statusFrame(undefined)), {
      patch: () => {},
      onStatus: (label) => labels.push(label),
    })

    expect(labels).toEqual([])
  })

  it("does not require a sink that cares about progress", async () => {
    // A rejoin consumer renders a replay and has no live run to report on; it
    // must not throw for a frame it has no use for.
    const result = await consumeChatStream(
      streamOf(statusFrame("Checking evidence"), '0:"ok"'),
      { patch: () => {} }
    )

    expect(result.content).toBe("ok")
  })
})
