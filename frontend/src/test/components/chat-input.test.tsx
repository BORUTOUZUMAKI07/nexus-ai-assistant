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
    })
  })

  it("includes enabled web/code toggles in the options", () => {
    render(<ChatInput onSendMessage={onSendMessage} isLoading={false} />)
    fireEvent.click(screen.getByTitle("Firecrawl Live Web Search"))
    fireEvent.click(screen.getByTitle("E2B Code Interpreter microVM"))

    const textarea = screen.getByPlaceholderText(
      "Ask Nexus anything, write code, search live web..."
    )
    fireEvent.change(textarea, { target: { value: "Research this" } })
    fireEvent.click(screen.getByTitle("Send message (Enter)"))

    expect(onSendMessage).toHaveBeenCalledWith("Research this", {
      enableWeb: true,
      enableCode: true,
      attachments: [],
    })
  })

  it("shows a stop button while loading and wires onStop", () => {
    render(<ChatInput onSendMessage={onSendMessage} isLoading={true} onStop={onStop} />)
    fireEvent.click(screen.getByTitle("Stop generation"))
    expect(onStop).toHaveBeenCalledTimes(1)
    expect(screen.queryByTitle("Send message (Enter)")).not.toBeInTheDocument()
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
})