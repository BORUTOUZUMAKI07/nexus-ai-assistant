import { describe, it, expect } from "vitest"
import { render, screen, fireEvent, waitFor } from "@/test/test-utils"
import { KnowledgeView } from "@/components/KnowledgeView"
import { UsageView } from "@/components/UsageView"
import { AdminView } from "@/components/AdminView"
import { setAccessToken } from "@/lib/auth"

describe("resilience - real error states (no fabricated fallbacks)", () => {
  it("KnowledgeView shows an error state with retry when /api/files returns 500", async () => {
    setAccessToken("tok")
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(http.get("/api/files", () => HttpResponse.json({ detail: "boom" }, { status: 500 })))

    render(<KnowledgeView />)

    expect(await screen.findByText("Fetch files failed: 500")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Retry/ })).toBeInTheDocument()
    expect(screen.queryByText("Nexus_Architecture_Master_Spec.pdf")).not.toBeInTheDocument()
  })

  it("KnowledgeView shows an error state on network error", async () => {
    setAccessToken("tok")
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(http.get("/api/files", () => HttpResponse.error()))

    render(<KnowledgeView />)

    expect(await screen.findByText(/Failed to fetch/)).toBeInTheDocument()
    expect(screen.queryByText("Production_AI_Design_Patterns.md")).not.toBeInTheDocument()
  })

  it("KnowledgeView surfaces a search error without inventing citations", async () => {
    setAccessToken("tok")
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.post("/api/files/rag/query", () => HttpResponse.json({ detail: "down" }, { status: 503 }))
    )

    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")
    fireEvent.change(
      screen.getByPlaceholderText("Ask a question about your documents…"),
      { target: { value: "Hybrid Search" } }
    )
    fireEvent.click(screen.getByText("Search"))

    await waitFor(() => expect(screen.getByText("RAG query failed: 503")).toBeInTheDocument())
    expect(screen.queryByText(/Native Hybrid Search uses multi-stage prefetch/)).not.toBeInTheDocument()
  })

  it("KnowledgeView reports an upload error instead of a local preview", async () => {
    setAccessToken("tok")
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.post("/api/files/upload", () => HttpResponse.json({ detail: "boom" }, { status: 500 }))
    )

    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(["content"], "offline.txt", { type: "text/plain" })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => expect(screen.getByText("Upload failed: 500")).toBeInTheDocument())
    expect(screen.queryByText("offline.txt")).not.toBeInTheDocument()
  })

  it("KnowledgeView keeps the row when delete fails and reports the error", async () => {
    setAccessToken("tok")
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.delete("/api/files/:id", () => HttpResponse.json({ detail: "boom" }, { status: 500 }))
    )

    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")
    fireEvent.click(screen.getByTitle("Delete document"))

    await waitFor(() => expect(screen.getByText("Delete file failed: 500")).toBeInTheDocument())
    expect(screen.getByText("nexus-spec.pdf")).toBeInTheDocument()
  })

  it("UsageView shows an error banner when the usage endpoint fails", async () => {
    setAccessToken("tok")
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.get("/api/usage/summary", () => HttpResponse.json({ detail: "boom" }, { status: 500 }))
    )

    render(<UsageView />)

    expect(await screen.findByText("Fetch usage failed: 500")).toBeInTheDocument()
    expect(screen.queryByText("142,850")).not.toBeInTheDocument()
  })

  it("AdminView shows an error banner when the users endpoint fails", async () => {
    setAccessToken("tok")
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(http.get("/api/admin/users", () => HttpResponse.json({}, { status: 500 })))

    render(<AdminView />)

    expect(await screen.findByText("Users endpoint failed: 500")).toBeInTheDocument()
    expect(screen.queryByText("Staff Engineer")).not.toBeInTheDocument()
  })

  it("AdminView shows an error banner when the audit endpoint fails", async () => {
    setAccessToken("tok")
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.get("/api/admin/audit-logs", () => HttpResponse.json({}, { status: 500 }))
    )

    render(<AdminView />)
    fireEvent.click(screen.getByText("Audit logs"))

    expect(await screen.findByText("Audit logs failed: 500")).toBeInTheDocument()
    expect(screen.queryByText("FILE_UPLOAD")).not.toBeInTheDocument()
  })
})