import { describe, it, expect, vi, beforeEach } from "vitest"
import { http, HttpResponse } from "msw"
import { fireEvent, render, screen, waitFor } from "@/test/test-utils"
import { server } from "@/test/mocks/server"
import { AdminView } from "@/features/admin/AdminView"
import { SettingsView } from "@/features/settings/SettingsView"
import { KnowledgeView } from "@/features/workspace/KnowledgeView"

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }))

vi.mock("sonner", () => ({
  toast: {
    error: (...args: unknown[]) => toastError(...args),
    success: vi.fn(),
    warning: vi.fn(),
  },
}))

describe("mutation failure feedback", () => {
  beforeEach(() => {
    toastError.mockClear()
    vi.spyOn(window, "confirm").mockReturnValue(true)
  })

  it("asks for destructive hook deletion in an accessible dialog", async () => {
    render(<AdminView />)
    fireEvent.click(screen.getByText("Lifecycle hooks"))
    await screen.findByText("Block shell exec")

    fireEvent.click(screen.getByTitle("Delete policy"))
    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveAttribute("aria-modal", "true")
    expect(screen.getByText(/stops running for every conversation/i)).toBeInTheDocument()
    // The name appears both in the list and in the confirmation copy; the
    // important part is that cancelling leaves the policy in the list.
    expect(screen.getAllByText("Block shell exec").length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }))
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
    expect(screen.getByRole("cell", { name: "Block shell exec" })).toBeInTheDocument()
  })

  it("toasts and announces a failed hook delete", async () => {
    server.use(
      http.delete("/api/admin/hooks/:id", () => new HttpResponse(null, { status: 500 })),
    )
    render(<AdminView />)
    fireEvent.click(screen.getByText("Lifecycle hooks"))
    await screen.findByText("Block shell exec")

    fireEvent.click(screen.getByTitle("Delete policy"))
    fireEvent.click(await screen.findByRole("button", { name: /yes, delete policy/i }))

    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(screen.getByRole("alert")).toHaveTextContent(/hook delete failed/i)
    expect(screen.getByText("Block shell exec")).toBeInTheDocument()
  })

  it("toasts and announces a failed memory delete in settings", async () => {
    server.use(
      http.delete("/api/settings/memories/:id", () => new HttpResponse(null, { status: 500 })),
    )
    render(<SettingsView />)
    const memory = await screen.findByText(/Prefers Python and TypeScript/)
    const deleteBtn = memory.closest("div")!.parentElement!.querySelector("button")!
    fireEvent.click(deleteBtn)

    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not delete memory/i)
    expect(screen.getByText(/Prefers Python and TypeScript/)).toBeInTheDocument()
  })

  it("toasts and announces a failed knowledge upload", async () => {
    server.use(
      http.post("/api/files/upload", () => new HttpResponse(null, { status: 500 })),
    )
    render(<KnowledgeView />)
    await screen.findByText("Upload a document")

    const file = new File(["x".repeat(2048)], "notes.txt", { type: "text/plain" })
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(await screen.findByRole("alert")).toHaveTextContent(/upload failed/i)
  })

  it("toasts and announces a failed semantic search", async () => {
    server.use(
      http.post("/api/files/rag/query", () => new HttpResponse(null, { status: 500 })),
    )
    render(<KnowledgeView />)
    await screen.findByText("Semantic search")
    fireEvent.change(screen.getByPlaceholderText(/ask a question about your documents/i), {
      target: { value: "What is RAG?" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Search" }))

    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(await screen.findByRole("alert")).toHaveTextContent(/search failed/i)
  })
})
