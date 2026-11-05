import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent, waitFor } from "@/test/test-utils"
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

  it("says an open conversation is empty instead of showing the new-chat hero", () => {
    // Measured live: selecting a conversation whose turns had all been deleted
    // rendered the *new chat* screen, so the selection looked like it did
    // nothing at all.
    render(
      <ChatArea messages={[]} isLoading={false} conversationTitle="Sweep renamed chat" />
    )
    expect(screen.getByText("Sweep renamed chat")).toBeInTheDocument()
    expect(
      screen.getByText(/No messages in this conversation yet/i)
    ).toBeInTheDocument()
    expect(screen.queryByText("How can Nexus help you today?")).not.toBeInTheDocument()
  })

  it("does not show a run verdict when there is no answer to describe", () => {
    // A branched conversation holds the user turn only, but the parent's
    // verdict was still rendered underneath it (live defect D5).
    render(
      <ChatArea
        messages={[{ id: "u1", role: "user", content: "branch base" }]}
        isLoading={false}
        conversationTitle="branch base (Branch: Forked Branch)"
        turnVerdict={{ revision_count: 0, evidence_score: 0.3, evidence_gate_passed: true }}
      />
    )
    expect(
      screen.queryByText("Answer passed review on first draft")
    ).not.toBeInTheDocument()
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

  it("labels an in-flight but still-empty assistant bubble as Thinking", () => {
    render(
      <ChatArea
        messages={[{ id: "a2", role: "assistant" as const, content: "" }]}
        isLoading={true}
      />
    )
    expect(screen.getByText("Thinking…")).toBeInTheDocument()
    expect(screen.queryByText("Writing…")).not.toBeInTheDocument()
  })

  it("shows the backend's current stage instead of a static Thinking", () => {
    // The synthesizer streams only its finished text, so an agent-mode run is
    // silent for most of a minute. The stage label is the only progress the
    // screen can show in that window; without it the chip below reads
    // "Thinking…" for the whole run and looks like a hung request.
    render(
      <ChatArea
        messages={[{ id: "a4", role: "assistant" as const, content: "" }]}
        isLoading={true}
        statusLabel="Checking evidence"
      />
    )
    expect(screen.getByText("Checking evidence")).toBeInTheDocument()
    expect(screen.queryByText("Thinking…")).not.toBeInTheDocument()
  })

  it("falls back to Thinking when no stage has been reported", () => {
    render(
      <ChatArea
        messages={[{ id: "a5", role: "assistant" as const, content: "" }]}
        isLoading={true}
        statusLabel={null}
      />
    )
    expect(screen.getByText("Thinking…")).toBeInTheDocument()
  })

  it("stops showing the stage once the answer starts arriving", () => {
    render(
      <ChatArea
        messages={[{ id: "a6", role: "assistant" as const, content: "Partial" }]}
        isLoading={true}
        statusLabel="Writing the answer"
      />
    )
    expect(screen.getByText("Writing…")).toBeInTheDocument()
    expect(screen.queryByText("Writing the answer")).not.toBeInTheDocument()
  })

  it("labels a streaming assistant bubble with content as Writing", () => {
    render(
      <ChatArea
        messages={[{ id: "a3", role: "assistant" as const, content: "Once upon a" }]}
        isLoading={true}
      />
    )
    // The label switches on the same piece of state the answer itself uses, so
    // the two cannot disagree about whether tokens have started landing.
    expect(screen.getByText("Writing…")).toBeInTheDocument()
    expect(screen.queryByText("Thinking…")).not.toBeInTheDocument()
  })

  it("does not label a finished assistant message as still writing", () => {
    render(<ChatArea messages={[assistant]} isLoading={false} />)
    expect(screen.queryByText("Writing…")).not.toBeInTheDocument()
    expect(screen.queryByText("Thinking…")).not.toBeInTheDocument()
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

  it("renders the HITL approval card for a parked high-impact action", () => {
    // The card is the whole point of HITL from the user's side: the backend can
    // park the run and persist the request, but if this block is missing the
    // user sees a finished-looking turn with the action silently skipped.
    render(
      <ChatArea
        messages={[]}
        isLoading={false}
        pendingHITL={{
          thread_id: "conv-1",
          request: "The agent wants to run a shell command with os.system.",
          tool_name: "execute_python",
          arguments: { code: "import os; os.system('ls')" },
        }}
        onResolveHITL={vi.fn()}
      />
    )
    expect(screen.getByText("Human Approval Required (Harness Layer 3)")).toBeInTheDocument()
    expect(
      screen.getByText("The agent wants to run a shell command with os.system.")
    ).toBeInTheDocument()
    expect(screen.getByText("execute_python")).toBeInTheDocument()
    expect(screen.getByText("High-Impact Action")).toBeInTheDocument()
    expect(screen.getByText("Approve & Continue")).toBeInTheDocument()
    expect(screen.getByText("Reject")).toBeInTheDocument()
    expect(screen.getByText("Modify…")).toBeInTheDocument()
  })

  it("fires onResolveHITL and retires the card once the decision lands", async () => {
    const onResolveHITL = vi.fn().mockResolvedValue(undefined)
    render(
      <ChatArea
        messages={[]}
        isLoading={false}
        pendingHITL={{
          thread_id: "conv-1",
          request: "Approve running the command?",
          tool_name: "execute_python",
        }}
        onResolveHITL={onResolveHITL}
      />
    )
    fireEvent.click(screen.getByText("Approve & Continue"))
    expect(onResolveHITL).toHaveBeenCalledWith("approve", undefined)
    await waitFor(() =>
      expect(
        screen.queryByText("Human Approval Required (Harness Layer 3)")
      ).not.toBeInTheDocument()
    )
  })

  it("keeps the approval card when the resolve fails, so the decision is not lost", async () => {
    // A failed submit must NOT retire the card. The hook re-throws precisely so
    // this component can tell failure from success; if the card collapsed here
    // the user would be left with an error and no Approve/Reject UI -- the same
    // "looks finished but produced nothing" hole the card exists to close.
    // Load-bearing: with the hook swallowing the error (no throw), `setResolved`
    // runs and the card is gone, so this assertion fails.
    const onResolveHITL = vi
      .fn()
      .mockRejectedValue(new Error("HITL feedback failed: 500"))
    render(
      <ChatArea
        messages={[]}
        isLoading={false}
        pendingHITL={{
          thread_id: "conv-1",
          request: "Approve running the command?",
          tool_name: "execute_python",
        }}
        onResolveHITL={onResolveHITL}
      />
    )
    fireEvent.click(screen.getByText("Approve & Continue"))
    await waitFor(() =>
      expect(onResolveHITL).toHaveBeenCalledWith("approve", undefined)
    )
    expect(
      screen.getByText("Human Approval Required (Harness Layer 3)")
    ).toBeInTheDocument()
    expect(screen.getByText("Approve & Continue")).toBeInTheDocument()
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

  // ── per-message action toolbars ─────────────────────────────────────────

  it("shows Regenerate and Branch on an assistant turn and wires both", async () => {
    const onRegenerate = vi.fn()
    const onBranch = vi.fn()
    render(
      <ChatArea
        messages={[assistant]}
        isLoading={false}
        onRegenerate={onRegenerate}
        onBranch={onBranch}
      />
    )

    fireEvent.click(screen.getByTitle("Regenerate answer"))
    expect(onRegenerate).toHaveBeenCalledWith("a1")
    // `runAction` disables the whole toolbar while an action is in flight, so
    // the re-enable lands a microtask later. Clicking Branch before that would
    // land on a disabled button and silently do nothing -- wait for it.
    await waitFor(() =>
      expect(screen.getByTitle("Branch conversation from here")).not.toBeDisabled()
    )

    fireEvent.click(screen.getByTitle("Branch conversation from here"))
    expect(onBranch).toHaveBeenCalledWith("a1")
  })

  it("shows Edit / Branch / Delete on a user turn and wires delete and branch", async () => {
    const onDeleteMessage = vi.fn()
    const onBranch = vi.fn()
    render(
      <ChatArea
        messages={[{ id: "u1", role: "user", content: "Tell me about RAG" }]}
        isLoading={false}
        onEditMessage={vi.fn()}
        onDeleteMessage={onDeleteMessage}
        onBranch={onBranch}
      />
    )

    fireEvent.click(screen.getByTitle("Delete message (and the turns after it)"))
    expect(onDeleteMessage).toHaveBeenCalledWith("u1")
    // Same in-flight disable as the assistant toolbar: wait for the re-enable
    // before the next click or it lands on a disabled button.
    await waitFor(() =>
      expect(screen.getByTitle("Branch conversation from here")).not.toBeDisabled()
    )

    fireEvent.click(screen.getByTitle("Branch conversation from here"))
    expect(onBranch).toHaveBeenCalledWith("u1")
  })

  it("opens an inline editor pre-filled, then submits with Save & resend", async () => {
    const onEditMessage = vi.fn()
    render(
      <ChatArea
        messages={[{ id: "u1", role: "user", content: "Tell me about RAG" }]}
        isLoading={false}
        onEditMessage={onEditMessage}
      />
    )

    fireEvent.click(screen.getByTitle("Edit & resend (discards later turns)"))
    const editor = screen.getByLabelText("Edit message") as HTMLTextAreaElement
    // Pre-filled with the existing text so the edit is a correction, not a
    // retype. This is the whole point of an inline editor over a prompt.
    expect(editor.value).toBe("Tell me about RAG")

    fireEvent.change(editor, { target: { value: "Tell me about hybrid RAG" } })
    fireEvent.click(screen.getByText("Save & resend"))

    await waitFor(() =>
      expect(onEditMessage).toHaveBeenCalledWith("u1", "Tell me about hybrid RAG")
    )
  })

  it("submits the inline editor on Enter and abandons it on Escape", async () => {
    const onEditMessage = vi.fn()
    render(
      <ChatArea
        messages={[{ id: "u1", role: "user", content: "original" }]}
        isLoading={false}
        onEditMessage={onEditMessage}
      />
    )

    fireEvent.click(screen.getByTitle("Edit & resend (discards later turns)"))
    const editor = screen.getByLabelText("Edit message")
    fireEvent.change(editor, { target: { value: "edited via Enter" } })
    fireEvent.keyDown(editor, { key: "Enter", shiftKey: false })
    await waitFor(() =>
      expect(onEditMessage).toHaveBeenCalledWith("u1", "edited via Enter")
    )

    // Escape abandons without calling the callback again.
    fireEvent.click(screen.getByTitle("Edit & resend (discards later turns)"))
    const editor2 = screen.getByLabelText("Edit message")
    fireEvent.keyDown(editor2, { key: "Escape" })
    expect(screen.queryByLabelText("Edit message")).not.toBeInTheDocument()
    expect(onEditMessage).toHaveBeenCalledTimes(1)
  })

  it("disables the submit button when the edited text is only whitespace", () => {
    render(
      <ChatArea
        messages={[{ id: "u1", role: "user", content: "original" }]}
        isLoading={false}
        onEditMessage={vi.fn()}
      />
    )
    fireEvent.click(screen.getByTitle("Edit & resend (discards later turns)"))
    const editor = screen.getByLabelText("Edit message")
    fireEvent.change(editor, { target: { value: "   " } })
    // An empty resend would be a delete wearing an edit's clothes, so it is
    // refused at the button rather than in the handler.
    expect(screen.getByText("Save & resend")).toBeDisabled()
  })

  it("omits a message action when its callback is not supplied", () => {
    render(<ChatArea messages={[assistant]} isLoading={false} />)
    // No onRegenerate / onBranch means no buttons -- the toolbar is built from
    // the callbacks, so a read-only view does not offer an action it cannot run.
    expect(screen.queryByTitle("Regenerate answer")).not.toBeInTheDocument()
    expect(
      screen.queryByTitle("Branch conversation from here")
    ).not.toBeInTheDocument()
  })
})