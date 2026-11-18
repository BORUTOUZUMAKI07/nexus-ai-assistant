import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@/test/test-utils"
import { ChatInput } from "@/features/chat/ChatInput"

describe("ChatInput accessibility and shortcuts", () => {
  const onSendMessage = vi.fn()
  const onStop = vi.fn()

  beforeEach(() => {
    onSendMessage.mockReset()
    onStop.mockReset()
  })

  it.each([
    ["metaKey", { metaKey: true }],
    ["ctrlKey", { ctrlKey: true }],
  ])("sends the draft with Cmd/Ctrl+Enter (%s)", (_name, modifier) => {
    render(<ChatInput onSendMessage={onSendMessage} isLoading={false} />)
    const textarea = screen.getByPlaceholderText(
      "Ask Nexus anything, write code, search live web..."
    )
    fireEvent.change(textarea, { target: { value: "Ship it" } })
    fireEvent.keyDown(textarea, { key: "Enter", ...modifier })

    expect(onSendMessage).toHaveBeenCalledWith(
      "Ship it",
      expect.objectContaining({ agentMode: "deep" })
    )
    expect((textarea as HTMLTextAreaElement).value).toBe("")
  })

  it("exposes the slash-command popover as a labelled listbox driven by the composer", () => {
    render(<ChatInput onSendMessage={onSendMessage} isLoading={false} />)
    const textarea = screen.getByPlaceholderText(
      "Ask Nexus anything, write code, search live web..."
    )
    fireEvent.change(textarea, { target: { value: "/" } })

    const listbox = screen.getByRole("listbox", { name: /slash commands/i })
    const options = screen.getAllByRole("option")
    expect(options).toHaveLength(7)
    expect(options[0]).toHaveAttribute("aria-selected", "true")
    expect(textarea).toHaveAttribute("aria-controls", listbox.id)
    expect(textarea).toHaveAttribute("aria-activedescendant", options[0].id)

    fireEvent.keyDown(textarea, { key: "ArrowDown" })
    expect(options[1]).toHaveAttribute("aria-selected", "true")
    expect(textarea).toHaveAttribute("aria-activedescendant", options[1].id)
  })

  it("gives every icon-only composer control an accessible name", () => {
    render(<ChatInput onSendMessage={onSendMessage} isLoading={true} onStop={onStop} />)

    expect(screen.getByRole("button", { name: "Attach document or code" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Attach image for vision analysis" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Generate an image from a prompt" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Voice input" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Stop generation" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Queue message" })).toBeInTheDocument()
  })
})
