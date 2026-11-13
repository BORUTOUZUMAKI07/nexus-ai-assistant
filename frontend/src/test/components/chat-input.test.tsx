import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@/test/test-utils"
import { ChatInput } from "@/features/chat/ChatInput"

describe("ChatInput", () => {
  const onSendMessage = vi.fn()
  const onStop = vi.fn()

  beforeEach(() => {
    onSendMessage.mockReset()
    onStop.mockReset()
  })

  it("disables the send button when the input is empty", () => {
    render(<ChatInput onSendMessage={onSendMessage} isLoading={false} />)
    expect(screen.getByTitle("Send message (Enter)")).toBeDisabled()
  })

  it("sends the message and resets the textarea", () => {
    render(<ChatInput onSendMessage={onSendMessage} isLoading={false} />)
    const textarea = screen.getByPlaceholderText(
      "Ask Nexus anything, write code, search live web..."
    )
    fireEvent.change(textarea, { target: { value: "Hello" } })
    fireEvent.click(screen.getByTitle("Send message (Enter)"))

    expect(onSendMessage).toHaveBeenCalledWith("Hello", {
      enableWeb: false,
      enableCode: false,
      attachments: [],
      agentMode: "deep",
      imageDataUrl: undefined,
      planMode: false,
      generateImage: false,
    })
    expect((textarea as HTMLTextAreaElement).value).toBe("")
  })

  it("sends on Enter without Shift", () => {
    render(<ChatInput onSendMessage={onSendMessage} isLoading={false} />)
    const textarea = screen.getByPlaceholderText(
      "Ask Nexus anything, write code, search live web..."
    )
    fireEvent.change(textarea, { target: { value: "Hi there" } })
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false })

    expect(onSendMessage).toHaveBeenCalledWith("Hi there", {
      enableWeb: false,
      enableCode: false,
      attachments: [],
      agentMode: "deep",
      imageDataUrl: undefined,
      planMode: false,
      generateImage: false,
    })
  })

  it("keeps Web, Code and Plan mutually exclusive in the sent options", () => {
    // The composer used to light both Web and Code, while
    // `resolveConversationMode` silently picked one (Code). The user saw "Web"
    // on a run the backend recorded as `code`. Exclusivity is the contract now:
    // turning one on clears the others, and that is what travels in `options`.
    render(<ChatInput onSendMessage={onSendMessage} isLoading={false} />)
    const textarea = screen.getByPlaceholderText(
      "Ask Nexus anything, write code, search live web..."
    )
    const send = (value: string) => {
      fireEvent.change(textarea, { target: { value } })
      fireEvent.click(screen.getByTitle("Send message (Enter)"))
    }

    fireEvent.click(screen.getByTitle("Live web search"))
    send("Research this")
    expect(onSendMessage).toHaveBeenLastCalledWith(
      "Research this",
      expect.objectContaining({ enableWeb: true, enableCode: false, planMode: false })
    )

    // Turning Code on clears Web rather than joining it.
    fireEvent.click(screen.getByTitle("Code interpreter sandbox"))
    send("Write a function")
    expect(onSendMessage).toHaveBeenLastCalledWith(
      "Write a function",
      expect.objectContaining({ enableWeb: false, enableCode: true, planMode: false })
    )

    // Plan clears both: page.tsx short-circuits to plan drafting before the
    // mode is even resolved, so a lit Web/Code would have been a lie.
    fireEvent.click(screen.getByTitle("Plan mode OFF: respond directly"))
    send("Draft a rollout plan")
    expect(onSendMessage).toHaveBeenLastCalledWith(
      "Draft a rollout plan",
      expect.objectContaining({ enableWeb: false, enableCode: false, planMode: true })
    )
  })

  it("passes planMode true when the Plan toggle is enabled", () => {
    render(<ChatInput onSendMessage={onSendMessage} isLoading={false} />)
    fireEvent.click(screen.getByTitle("Plan mode OFF: respond directly"))

    const textarea = screen.getByPlaceholderText(
      "Ask Nexus anything, write code, search live web..."
    )
    fireEvent.change(textarea, { target: { value: "Refactor the auth layer" } })
    fireEvent.click(screen.getByTitle("Send message (Enter)"))

    expect(onSendMessage).toHaveBeenCalledWith("Refactor the auth layer", {
      enableWeb: false,
      enableCode: false,
      attachments: [],
      agentMode: "deep",
      imageDataUrl: undefined,
      planMode: true,
      generateImage: false,
    })
  })

  it("shows a stop button while loading and wires onStop", () => {
    render(<ChatInput onSendMessage={onSendMessage} isLoading={true} onStop={onStop} />)
    fireEvent.click(screen.getByTitle("Stop generation"))
    expect(onStop).toHaveBeenCalledTimes(1)
    expect(screen.queryByTitle("Send message (Enter)")).not.toBeInTheDocument()
  })

  it("disables the send button while loading so a still-streaming run is not re-hit (409)", () => {
    // A run is already in flight. The backend already rejects a second stream
    // with 409, so the UI must disable send rather than let the user hit the
    // guard. The message stays in the composer; the active run's `finally`
    // starts it when it finishes.
    render(<ChatInput onSendMessage={onSendMessage} isLoading={true} onStop={onStop} />)
    const textarea = screen.getByPlaceholderText(
      "Ask Nexus anything, write code, search live web..."
    )
    fireEvent.change(textarea, { target: { value: "follow up" } })

    const sendButton = screen.getByTitle("Queue message (Enter)")
    expect(sendButton).toBeDisabled()
    fireEvent.click(sendButton)

    expect(onSendMessage).not.toHaveBeenCalled()
    expect((textarea as HTMLTextAreaElement).value).toBe("follow up")
  })

  it("renders queued messages and cancels one", () => {
    const onCancelQueued = vi.fn()
    render(
      <ChatInput
        onSendMessage={onSendMessage}
        isLoading={true}
        onStop={onStop}
        queuedMessages={[
          { id: "q1", content: "queued one" },
          { id: "q2", content: "queued two" },
        ]}
        onCancelQueued={onCancelQueued}
      />
    )
    expect(screen.getByText("queued one")).toBeInTheDocument()
    expect(screen.getByText("queued two")).toBeInTheDocument()

    fireEvent.click(screen.getAllByTitle("Remove queued message")[0])
    expect(onCancelQueued).toHaveBeenCalledWith("q1")
  })

  it("renders attachment chips and allows removing them", () => {
    render(<ChatInput onSendMessage={onSendMessage} isLoading={false} />)
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(["content"], "notes.md", { type: "text/markdown" })
    fireEvent.change(fileInput, { target: { files: [file] } })

    expect(screen.getByText("notes.md")).toBeInTheDocument()

    fireEvent.click(screen.getByText("notes.md").closest("div")!.querySelector("button")!)
    expect(screen.queryByText("notes.md")).not.toBeInTheDocument()
  })

  it("sets generateImage when the /image slash command is used", () => {
    render(<ChatInput onSendMessage={onSendMessage} isLoading={false} />)
    const textarea = screen.getByPlaceholderText(
      "Ask Nexus anything, write code, search live web..."
    )
    fireEvent.change(textarea, { target: { value: "/image" } })
    // The slash popover lists the command; applying it flips image mode and
    // clears the typed command so the prompt itself is what gets sent.
    fireEvent.mouseDown(screen.getByText("Generate Image"))
    fireEvent.change(textarea, { target: { value: "a red panda" } })
    fireEvent.click(screen.getByTitle("Send message (Enter)"))

    expect(onSendMessage).toHaveBeenCalledWith(
      "a red panda",
      expect.objectContaining({ generateImage: true })
    )
  })

  it("sets generateImage when the image-mode toolbar button is toggled", () => {
    render(<ChatInput onSendMessage={onSendMessage} isLoading={false} />)
    fireEvent.click(screen.getByTitle("Generate an image from a prompt"))
    const textarea = screen.getByPlaceholderText(
      "Describe the image you want Nexus to generate..."
    )
    fireEvent.change(textarea, { target: { value: "a blue whale" } })
    fireEvent.click(screen.getByTitle("Send message (Enter)"))

    expect(onSendMessage).toHaveBeenCalledWith(
      "a blue whale",
      expect.objectContaining({ generateImage: true })
    )
  })

  it("resets image mode after sending", () => {
    render(<ChatInput onSendMessage={onSendMessage} isLoading={false} />)
    fireEvent.click(screen.getByTitle("Generate an image from a prompt"))
    const textarea = screen.getByPlaceholderText(
      "Describe the image you want Nexus to generate..."
    )
    fireEvent.change(textarea, { target: { value: "first" } })
    fireEvent.click(screen.getByTitle("Send message (Enter)"))

    // Back to chat mode: the normal placeholder returns and a plain send no
    // longer carries generateImage.
    expect(
      screen.getByPlaceholderText("Ask Nexus anything, write code, search live web...")
    ).toBeInTheDocument()
    fireEvent.change(textarea, { target: { value: "second" } })
    fireEvent.click(screen.getByTitle("Send message (Enter)"))
    expect(onSendMessage).toHaveBeenLastCalledWith(
      "second",
      expect.objectContaining({ generateImage: false })
    )
  })
})