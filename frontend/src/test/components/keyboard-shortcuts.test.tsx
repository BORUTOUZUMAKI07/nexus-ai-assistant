import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@/test/test-utils"
import { KeyboardShortcutsGuide } from "@/components/layout/KeyboardShortcuts"

describe("KeyboardShortcutsGuide", () => {
  it("documents only shortcuts the application actually implements", () => {
    render(<KeyboardShortcutsGuide open onClose={vi.fn()} />)

    expect(screen.getByRole("dialog", { name: /keyboard shortcuts/i })).toBeInTheDocument()
    expect(screen.getByText("Open command palette")).toBeInTheDocument()
    expect(screen.getByText("Send message")).toBeInTheDocument()
    expect(screen.getByText("Stop generation")).toBeInTheDocument()
    expect(screen.getByText("Toggle dark mode")).toBeInTheDocument()
    expect(screen.getByText("Show this help")).toBeInTheDocument()

    // The old copy advertised a cancel command that was never bound.
    expect(screen.queryByText("Cancel generation")).not.toBeInTheDocument()
  })

  it("is a focus-managed dialog that closes on Escape", () => {
    const onClose = vi.fn()
    render(
      <>
        <button type="button">Page behind dialog</button>
        <KeyboardShortcutsGuide open onClose={onClose} />
      </>
    )

    expect(document.activeElement).not.toBe(document.body)
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).toHaveBeenCalled()
  })

  it("renders nothing while closed", () => {
    render(<KeyboardShortcutsGuide open={false} onClose={vi.fn()} />)
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })
})
