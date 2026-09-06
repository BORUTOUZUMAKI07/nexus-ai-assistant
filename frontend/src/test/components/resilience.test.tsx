import { describe, it, expect } from "vitest"
import { render, screen, fireEvent, waitFor } from "@/test/test-utils"
import { KnowledgeView } from "@/components/KnowledgeView"
import { UsageView } from "@/components/UsageView"
import { AdminView } from "@/components/AdminView"
import { setAccessToken } from "@/lib/auth"

describe("resilience - degraded API fallbacks", () => {
  it("KnowledgeView falls back to sample documents when /api/files returns 500", async () => {
    setAccessToken("tok")
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(http.get("/api/files", () => HttpResponse.json({ detail: "boom" }, { status: 500 })))

    render(<KnowledgeView />)

    expect(await screen.findByText("Nexus_Architecture_Master_Spec.pdf")).toBeInTheDocument()
  })

  it("KnowledgeView falls back to sample documents on network error", async () => {
    setAccessToken("tok")
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(http.get("/api/files", () => HttpResponse.error()))

    render(<KnowledgeView />)

    expect(await screen.findByText("Production_AI_Design_Patterns.md")).toBeInTheDocument()
  })

  it("KnowledgeView RAG search degrades to sample citations on backend error", async () => {
    setAccessToken("tok")
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.post("/api/files/rag/query", () => HttpResponse.json({ detail: "down" }, { status: 503 }))
    )

    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")
    fireEvent.change(
      screen.getByPlaceholderText("Test a query against Qdrant (e.g., 'What is Hybrid Search?')"),
      { target: { value: "Hybrid Search" } }
    )
    fireEvent.click(screen.getByText("Search"))

    await waitFor(() =>
      expect(
        screen.getByText(/Native Hybrid Search uses multi-stage prefetch/)
      ).toBeInTheDocument()
    )
  })

  it("KnowledgeView shows a local preview item when upload fails", async () => {
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

    await waitFor(() => expect(screen.getByText("offline.txt")).toBeInTheDocument())
  })

  it("KnowledgeView removes the row optimistically when delete fails", async () => {
    setAccessToken("tok")
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.delete("/api/files/:id", () => HttpResponse.json({ detail: "boom" }, { status: 500 }))
    )

    render(<KnowledgeView />)
    await screen.findByText("nexus-spec.pdf")
    fireEvent.click(screen.getByTitle("Delete document"))

    await waitFor(() => expect(screen.queryByText("nexus-spec.pdf")).not.toBeInTheDocument())
  })

  it("UsageView shows free-tier estimates when the usage endpoint fails", async () => {
    setAccessToken("tok")
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.get("/api/usage/summary", () => HttpResponse.json({ detail: "boom" }, { status: 500 }))
    )

    render(<UsageView />)

    expect(await screen.findByText("142,850")).toBeInTheDocument()
    expect(screen.getByText("$0.00")).toBeInTheDocument()
    expect(screen.getByText("Protected")).toBeInTheDocument()
    expect(screen.getByText("Zero API Expense")).toBeInTheDocument()
  })

  it("AdminView falls back to mock users when the users endpoint returns non-ok", async () => {
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(http.get("/api/v1/admin/users", () => HttpResponse.json({}, { status: 500 })))

    render(<AdminView />)

    expect(await screen.findByText("Staff Engineer")).toBeInTheDocument()
    expect(screen.getByText("developer@nexus.ai")).toBeInTheDocument()
  })

  it("AdminView falls back to sample audit logs when the audit endpoint fails", async () => {
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.get("/api/v1/admin/audit-logs", () => HttpResponse.json({}, { status: 500 }))
    )

    render(<AdminView />)
    fireEvent.click(screen.getByText("Compliance Audit Logs"))

    expect(await screen.findByText("FILE_UPLOAD")).toBeInTheDocument()
  })
})