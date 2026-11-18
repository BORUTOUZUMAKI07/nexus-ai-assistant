import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, render, screen, waitFor } from "@/test/test-utils"
import { ChatInput } from "@/features/chat/ChatInput"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }))

vi.mock("sonner", () => ({
  toast: {
    error: (...args: unknown[]) => toastError(...args),
    success: vi.fn(),
    warning: vi.fn(),
  },
}))

function renderComposer() {
  return render(
    <ChatInput onSendMessage={vi.fn()} isLoading={false} />,
  )
}

describe("ChatInput toggle accessibility", () => {
  it("exposes pressed state for the Deep/Fast, Web, Code and Plan toggles", () => {
    renderComposer()
    const deepFast = screen.getByRole("button", { name: /deep agent|fast chat/i })
    expect(deepFast).toHaveAttribute("aria-pressed")

    for (const name of ["Web", "Code", "Plan"]) {
      const toggle = screen.getByRole("button", { name })
      expect(toggle, name).toHaveAttribute("aria-pressed", "false")
      fireEvent.click(toggle)
      expect(toggle, name).toHaveAttribute("aria-pressed", "true")
    }
  })

  it("reflects exclusive mode changes in aria-pressed", () => {
    renderComposer()
    const web = screen.getByRole("button", { name: "Web" })
    const code = screen.getByRole("button", { name: "Code" })
    fireEvent.click(web)
    expect(web).toHaveAttribute("aria-pressed", "true")
    fireEvent.click(code)
    expect(code).toHaveAttribute("aria-pressed", "true")
    expect(web).toHaveAttribute("aria-pressed", "false")
  })
})

describe("ChatInput voice errors", () => {
  beforeEach(() => {
    toastError.mockClear()
  })

  it("uses a toast instead of a blocking alert when the mic is denied", async () => {
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {})
    const getUserMedia = vi.fn().mockRejectedValue(new Error("denied"))
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    })

    renderComposer()
    fireEvent.click(screen.getByRole("button", { name: "Voice input" }))

    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(alertSpy).not.toHaveBeenCalled()
    alertSpy.mockRestore()
  })

  it("no longer calls alert() for transcription failures", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src", "features/chat/ChatInput.tsx"),
      "utf8",
    )
    expect(source).not.toMatch(/\balert\(/)
    expect(source).toContain("toast.error")
  })
})
