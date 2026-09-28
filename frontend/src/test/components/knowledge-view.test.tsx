import { describe, it, expect } from "vitest"
import { render, screen, fireEvent, waitFor } from "@/test/test-utils"
import { KnowledgeView } from "@/components/KnowledgeView"

function clearCookie() {
  document.cookie.split(";").forEach((c) => {
    document.cookie = `${c.split("=")[0].trim()}=; path=/; max-age=0`
  })
}

describe("KnowledgeView", () => {
  it("loads and lists indexed documents from the API", async () => {
    render(<KnowledgeView />)
    expect(await screen.findByText("nexus-spec.pdf")).toBeInTheDocument()
    expect(screen.getByText(/42 chunks/)).toBeInTheDocument()
  })

  it("deletes a document and removes it from the list", async () => {
    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")
    fireEvent.click(screen.getByTitle("Delete document"))
    await waitFor(() =>
      expect(screen.queryByText("nexus-spec.pdf")).not.toBeInTheDocument()
    )
  })

  it("uploads a file and prepends it to the list", async () => {
    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(["hello"], "uploaded.txt", { type: "text/plain" })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => expect(screen.getByText("uploaded.txt")).toBeInTheDocument())
  })

  it("runs a hybrid search and renders citations", async () => {
    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")

    fireEvent.change(
      screen.getByPlaceholderText("Ask a question about your documents…"),
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

describe("KnowledgeView search behaviour", () => {
  const search = (value: string) => {
    fireEvent.change(
      screen.getByPlaceholderText("Ask a question about your documents…"),
      { target: { value } }
    )
    fireEvent.click(screen.getByText("Search"))
  }

  // Asserts the wire contract only. Routing the call through ragQuery() rather
  // than a hand-rolled fetch is not observable here -- authHeaders() adds no
  // headers of its own -- so this test deliberately does not claim to prove it.
  it("sends a trimmed query and the default top_k", async () => {
    let sent: { query: string; top_k: number } | null = null
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.post("/api/files/rag/query", async ({ request }) => {
        sent = (await request.json()) as { query: string; top_k: number }
        return HttpResponse.json({ citations: [] })
      })
    )

    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")
    search("  embedding  ")

    await waitFor(() => expect(sent).not.toBeNull())
    // Trimmed before it goes out, and top_k is the helper's documented default.
    expect(sent).toEqual({ query: "embedding", top_k: 5 })
  })

  it("says so explicitly when a search returns no passages", async () => {
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.post("/api/files/rag/query", () => HttpResponse.json({ citations: [] }))
    )

    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")
    search("nothing matches this")

    await waitFor(() =>
      expect(
        screen.getByText("No matching passages in your indexed documents.")
      ).toBeInTheDocument()
    )
  })

  it("does not show the no-results message before any search has run", async () => {
    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")

    // Text typed but not yet submitted: not a result.
    fireEvent.change(
      screen.getByPlaceholderText("Ask a question about your documents…"),
      { target: { value: "typed but not searched" } }
    )
    expect(
      screen.queryByText("No matching passages in your indexed documents.")
    ).toBeNull()
  })

  it("does not send a blank query", async () => {
    let calls = 0
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.post("/api/files/rag/query", () => {
        calls += 1
        return HttpResponse.json({ citations: [] })
      })
    )

    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")
    search("   ")

    await waitFor(() => expect(screen.getByText("Search")).toBeInTheDocument())
    expect(calls).toBe(0)
  })

  it("surfaces a backend failure instead of a silent empty result", async () => {
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.post("/api/files/rag/query", () =>
        HttpResponse.json({ detail: "vector store offline" }, { status: 503 })
      )
    )

    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")
    search("anything")

    await waitFor(() =>
      expect(screen.getByText(/RAG query failed: 503/)).toBeInTheDocument()
    )
    // An error must not masquerade as "no results found".
    expect(
      screen.queryByText("No matching passages in your indexed documents.")
    ).toBeNull()
  })

  it("renders a missing score as n/a rather than inventing a number", async () => {
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.post("/api/files/rag/query", () =>
        HttpResponse.json({
          citations: [{ filename: "spec.pdf", chunk_index: 2, content_snippet: "text" }],
        })
      )
    )

    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")
    search("score me")

    await waitFor(() => expect(screen.getByText("Score: n/a")).toBeInTheDocument())
  })

  it("shows how long the search took when the backend reports it", async () => {
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.post("/api/files/rag/query", () =>
        HttpResponse.json({
          citations: [{ filename: "spec.pdf", chunk_index: 1, score: 0.5, content_snippet: "t" }],
          took_ms: 42,
        })
      )
    )

    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")
    search("timing")

    await waitFor(() => expect(screen.getByText(/in 42ms/)).toBeInTheDocument())
  })
})