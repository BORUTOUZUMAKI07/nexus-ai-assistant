import { describe, it, expect } from "vitest"
import { render, screen, fireEvent, waitFor } from "@/test/test-utils"
import { KnowledgeView } from "@/components/KnowledgeView"
import { setAccessToken } from "@/lib/auth"

function clearCookie() {
  document.cookie.split(";").forEach((c) => {
    document.cookie = `${c.split("=")[0].trim()}=; path=/; max-age=0`
  })
}

describe("KnowledgeView", () => {
  it("loads and lists indexed documents from the API", async () => {
    setAccessToken("tok")
    render(<KnowledgeView />)
    expect(await screen.findByText("nexus-spec.pdf")).toBeInTheDocument()
    expect(screen.getByText(/42 chunks/)).toBeInTheDocument()
  })

  it("deletes a document and removes it from the list", async () => {
    setAccessToken("tok")
    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")
    fireEvent.click(screen.getByTitle("Delete document"))
    await waitFor(() =>
      expect(screen.queryByText("nexus-spec.pdf")).not.toBeInTheDocument()
    )
  })

  it("uploads a file and prepends it to the list", async () => {
    setAccessToken("tok")
    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(["hello"], "uploaded.txt", { type: "text/plain" })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => expect(screen.getByText("uploaded.txt")).toBeInTheDocument())
  })

  it("runs a hybrid search and renders citations", async () => {
    setAccessToken("tok")
    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")

    fireEvent.change(
      screen.getByPlaceholderText("Test a query against Qdrant (e.g., 'What is Hybrid Search?')"),
      { target: { value: "Hybrid Search" } }
    )
    fireEvent.click(screen.getByText("Search"))

    await waitFor(() =>
      expect(screen.getByText(/combines dense embeddings and sparse BM25/)).toBeInTheDocument()
    )
  })

  it("shows the empty state when the backend lists no files", async () => {
    clearCookie()
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.get("/api/files", () => HttpResponse.json([]))
    )
    render(<KnowledgeView />)
    await waitFor(() =>
      expect(screen.getByText("No documents indexed yet. Upload one above.")).toBeInTheDocument()
    )
  })
})