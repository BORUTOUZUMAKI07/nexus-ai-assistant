import * as React from "react"
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { fireEvent, render, screen, waitFor } from "@/test/test-utils"
import { ModelComparison } from "@/features/chat/ModelComparison"
import { MODELS } from "@/lib/models"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"

function streamOf(text: string): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(`0:${JSON.stringify(text)}\n`))
      controller.close()
    },
  })
}

describe("ModelComparison dialog behaviour", () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString()
      if (url.startsWith("/api/conversations") && init?.method === "POST") {
        return new Response(JSON.stringify({ id: `conv-${Math.random()}` }), { status: 201 })
      }
      if (url.startsWith("/api/conversations/") && init?.method === "PATCH") {
        return new Response("{}", { status: 200 })
      }
      if (url.startsWith("/api/chat")) {
        return new Response(streamOf("answer"), { status: 200 })
      }
      return new Response("{}", { status: 200 })
    }) as unknown as typeof fetch
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    window.localStorage.clear()
  })

  function Harness({ onClose }: { onClose: () => void }) {
    return (
      <ModelComparison
        onClose={onClose}
        question="Explain RAG"
        modelA={MODELS[0].id}
        modelB={MODELS[1].id}
      />
    )
  }

  it("is a modal dialog with initial focus inside it", () => {
    render(<Harness onClose={() => {}} />)
    const dialog = screen.getByRole("dialog", { name: /model comparison/i })
    expect(dialog).toHaveAttribute("aria-modal", "true")
    expect(dialog.contains(document.activeElement)).toBe(true)
  })

  it("closes on Escape and restores focus to the trigger", async () => {
    function HarnessWithTrigger() {
      const [open, setOpen] = React.useState(false)
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>Open comparison</button>
          {open && (
            <ModelComparison
              onClose={() => setOpen(false)}
              question="Explain RAG"
              modelA={MODELS[0].id}
              modelB={MODELS[1].id}
            />
          )}
        </>
      )
    }
    render(<HarnessWithTrigger />)
    const trigger = screen.getByRole("button", { name: "Open comparison" })
    trigger.focus()
    fireEvent.click(trigger)

    expect(screen.getByRole("dialog")).toBeInTheDocument()
    fireEvent.keyDown(document, { key: "Escape" })

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
    await waitFor(() => expect(document.activeElement).toBe(trigger))
  })

  it("uses the shared scrim token rather than a hardcoded black backdrop", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src", "features/chat/ModelComparison.tsx"),
      "utf8",
    )
    expect(source).not.toMatch(/bg-black\/50/)
    expect(source).toContain("DialogContent")
  })
})
