import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@/test/test-utils"
import { ChatInput } from "@/components/ChatInput"

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

  it("includes enabled web/code toggles in the options", () => {
    render(<ChatInput onSendMessage={onSendMessage} isLoading={false} />)
    fireEvent.click(screen.getByTitle("Live web search"))
    fireEvent.click(screen.getByTitle("Code interpreter sandbox"))

    const textarea = screen.getByPlaceholderText(
      "Ask Nexus anything, write code, search live web..."
    )
    fireEvent.change(textarea, { target: { value: "Research this" } })
    fireEvent.click(screen.getByTitle("Send message (Enter)"))

    expect(onSendMessage).toHaveBeenCalledWith("Research this", {
      enableWeb: true,
      enableCode: true,
      attachments: [],
      agentMode: "deep",
      imageDataUrl: undefined,
      planMode: false,
      generateImage: false,
    })
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

  it("allows submitting while loading so the turn can be queued", () => {
    // The send button used to be absent while a run was in flight, so the
    // message was unreachable. It is now present and labelled as a queue.
    render(<ChatInput onSendMessage={onSendMessage} isLoading={true} onStop={onStop} />)
    const textarea = screen.getByPlaceholderText(
      "Ask Nexus anything, write code, search live web..."
    )
    fireEvent.change(textarea, { target: { value: "follow up" } })

    const queueButton = screen.getByTitle("Queue message (Enter)")
    expect(queueButton).not.toBeDisabled()
    fireEvent.click(queueButton)

    expect(onSendMessage).toHaveBeenCalledWith(
      "follow up",
      expect.objectContaining({ agentMode: "deep" })
    )
    expect((textarea as HTMLTextAreaElement).value).toBe("")
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