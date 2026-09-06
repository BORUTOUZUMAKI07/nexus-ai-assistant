import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@/test/test-utils"
import { ChatArea } from "@/components/ChatArea"

describe("ChatArea", () => {
  const assistant = {
    id: "a1",
    role: "assistant" as const,
    content: "Here is your answer.",
    model: "llama-3.3-70b-versatile",
    thought_process: "DeepSeek reasoning steps...",
    citations: [
      {
        filename: "nexus-spec.pdf",
        chunk_index: 4,
        score: 0.94,
        content_snippet: "Hybrid Search details.",
      },
    ],
    tool_calls: [
      { name: "web_search", args: {}, result: "https://example.com", status: "completed" },
    ],
  }

  it("shows an empty-state prompt when no messages exist", () => {
    render(<ChatArea messages={[]} isLoading={false} />)
    expect(screen.getByText("How can Nexus help you today?")).toBeInTheDocument()
  })

  it("renders user and assistant bubbles", () => {
    render(
      <ChatArea
        messages={[
          { id: "u1", role: "user", content: "Tell me about RAG" },
          assistant,
        ]}
        isLoading={false}
      />
    )
    expect(screen.getByText("Tell me about RAG")).toBeInTheDocument()
    expect(screen.getByText("Here is your answer.")).toBeInTheDocument()
  })

  it("displays a thought process drawer that toggles open", () => {
    render(<ChatArea messages={[assistant]} isLoading={false} />)
    expect(screen.queryByText("DeepSeek reasoning steps...")).not.toBeInTheDocument()
    fireEvent.click(screen.getByText("Thought Process"))
    expect(screen.getByText("DeepSeek reasoning steps...")).toBeInTheDocument()
    fireEvent.click(screen.getByText("Thought Process"))
    expect(screen.queryByText("DeepSeek reasoning steps...")).not.toBeInTheDocument()
  })

  it("renders tool calls and citations for assistant messages", () => {
    render(<ChatArea messages={[assistant]} isLoading={false} />)
    expect(screen.getByText("web_search")).toBeInTheDocument()
    expect(screen.getByText("completed")).toBeInTheDocument()
    expect(screen.getByText("Grounding Sources")).toBeInTheDocument()
    expect(screen.getByText("nexus-spec.pdf")).toBeInTheDocument()
    expect(screen.getByText("llama-3.3-70b-versatile")).toBeInTheDocument()
  })

  it("copies the assistant response via the clipboard", () => {
    const writeText = vi.fn()
    Object.assign(navigator, { clipboard: { writeText } })
    render(<ChatArea messages={[assistant]} isLoading={false} />)
    fireEvent.click(screen.getByTitle("Copy response"))
    expect(writeText).toHaveBeenCalledWith("Here is your answer.")
  })

  it("reports thumbs up/down feedback through onFeedback", () => {
    const onFeedback = vi.fn()
    render(<ChatArea messages={[assistant]} isLoading={false} onFeedback={onFeedback} />)
    fireEvent.click(screen.getByTitle("Good response"))
    expect(onFeedback).toHaveBeenCalledWith("a1", "thumbs_up")
    fireEvent.click(screen.getByTitle("Bad response"))
    expect(onFeedback).toHaveBeenCalledWith("a1", "thumbs_down")
  })

  it("shows the reasoning indicator while loading", () => {
    render(<ChatArea messages={[]} isLoading={true} />)
    expect(screen.getByText("Nexus is reasoning...")).toBeInTheDocument()
  })
})