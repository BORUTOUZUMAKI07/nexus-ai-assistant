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
    fireEvent.click(screen.getByText("Thought process"))
    expect(screen.getByText("DeepSeek reasoning steps...")).toBeInTheDocument()
    fireEvent.click(screen.getByText("Thought process"))
    expect(screen.queryByText("DeepSeek reasoning steps...")).not.toBeInTheDocument()
  })

  it("renders tool calls and citations for assistant messages", () => {
    render(<ChatArea messages={[assistant]} isLoading={false} />)
    expect(screen.getByText("web_search")).toBeInTheDocument()
    expect(screen.getByText("completed")).toBeInTheDocument()
    expect(screen.getByText("Grounding sources")).toBeInTheDocument()
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
    expect(screen.getByText("Nexus is working…")).toBeInTheDocument()
  })

  it("renders the pending plan review card with approve/reject/dismiss", () => {
    const plan = {
      id: "plan-1",
      title: "Refactor auth service",
      summary: "Split the session manager out of the auth service.",
      steps: ["Extract SessionManager", "Add unit tests"],
      status: "pending" as const,
    }
    render(
      <ChatArea
        messages={[]}
        isLoading={false}
        pendingPlan={plan}
        onApprovePlan={vi.fn()}
        onRejectPlan={vi.fn()}
        onDismissPlan={vi.fn()}
      />
    )
    expect(screen.getByText("Proposed Plan")).toBeInTheDocument()
    expect(screen.getByText("Refactor auth service")).toBeInTheDocument()
    expect(screen.getByText("Split the session manager out of the auth service.")).toBeInTheDocument()
    expect(screen.getByText("Approve & Execute")).toBeInTheDocument()
    expect(screen.getByText("Reject")).toBeInTheDocument()
    expect(screen.getByText("Dismiss")).toBeInTheDocument()
  })

  it("fires onApprovePlan / onRejectPlan / onDismissPlan from the plan card", () => {
    const onApprovePlan = vi.fn()
    const onRejectPlan = vi.fn()
    const onDismissPlan = vi.fn()
    const plan = {
      id: "plan-1",
      title: "Refactor auth service",
      summary: null,
      steps: ["Extract SessionManager"],
      status: "pending" as const,
    }
    render(
      <ChatArea
        messages={[]}
        isLoading={false}
        pendingPlan={plan}
        onApprovePlan={onApprovePlan}
        onRejectPlan={onRejectPlan}
        onDismissPlan={onDismissPlan}
      />
    )
    fireEvent.click(screen.getByText("Approve & Execute"))
    expect(onApprovePlan).toHaveBeenCalledWith(plan)
    fireEvent.click(screen.getByText("Reject"))
    expect(onRejectPlan).toHaveBeenCalledWith(plan)
    fireEvent.click(screen.getByText("Dismiss"))
    expect(onDismissPlan).toHaveBeenCalledTimes(1)
  })

  it("shows a Save button on detected code artifacts and wires onSaveArtifact", () => {
    const onSaveArtifact = vi.fn()
    const onOpenArtifact = vi.fn()
    const codeMessage = {
      id: "m1",
      role: "assistant" as const,
      content:
        "Here is the fix:\n\n```python\ndef hello(name: str) -> str:\n    greeting = \"Hello\"\n    return f\"{greeting}, {name}\"\n```",
      model: "llama-3.3-70b-versatile",
    }
    render(
      <ChatArea
        messages={[codeMessage]}
        isLoading={false}
        onOpenArtifact={onOpenArtifact}
        onSaveArtifact={onSaveArtifact}
      />
    )
    const artifactTitle = screen.getByText("PYTHON Snippet")
    expect(artifactTitle).toBeInTheDocument()
    fireEvent.click(screen.getByText("Save"))
    expect(onSaveArtifact).toHaveBeenCalledTimes(1)
    expect(onSaveArtifact.mock.calls[0][0]).toMatchObject({ language: "python" })
  })
})