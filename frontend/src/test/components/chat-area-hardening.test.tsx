import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@/test/test-utils"
import { ChatArea } from "@/features/chat/ChatArea"

const assistant = {
  id: "a1",
  role: "assistant" as const,
  content: "Here is your answer.",
  model: "llama-3.3-70b-versatile",
}

describe("ChatArea accessibility", () => {
  it("labels assistant actions and reveals the toolbar when it receives focus", () => {
    render(<ChatArea messages={[assistant]} isLoading={false} onFeedback={vi.fn()} />)

    const copy = screen.getByRole("button", { name: "Copy response" })
    expect(copy).toHaveAttribute("aria-label", "Copy response")
    expect(copy.parentElement!.className).toContain("focus-within:opacity-100")
    expect(screen.getByRole("button", { name: "Read aloud" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Good response" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Bad response" })).toBeInTheDocument()
  })

  it("labels user actions and reveals the user toolbar when it receives focus", () => {
    render(
      <ChatArea
        messages={[{ id: "u1", role: "user", content: "Hello" }]}
        isLoading={false}
        onEditMessage={vi.fn()}
        onDeleteMessage={vi.fn()}
      />
    )

    const edit = screen.getByRole("button", { name: "Edit and resend message" })
    expect(edit).toHaveAttribute("aria-label", "Edit and resend message")
    expect(edit.parentElement!.className).toContain("focus-within:opacity-100")
    expect(screen.getByRole("button", { name: "Delete message" })).toBeInTheDocument()
  })

  it("labels regeneration and branching controls when their handlers are present", () => {
    render(
      <ChatArea
        messages={[assistant]}
        isLoading={false}
        onRegenerate={vi.fn()}
        onBranch={vi.fn()}
      />
    )
    expect(screen.getByRole("button", { name: "Regenerate answer" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Branch conversation from here" })).toBeInTheDocument()
  })

  it("leaves room for the fixed mobile composer when messages scroll", () => {
    const { container } = render(<ChatArea messages={[assistant]} isLoading={false} />)
    const scroller = container.querySelector(".overflow-y-auto")!
    expect(scroller.className).toContain("pb-28")
  })
})
