import { test, expect, NexusAppPage, setupConversationMocks, setAuthState, mockApi } from "./fixtures"

const FILE = {
  id: "file-1",
  filename: "nexus-spec.pdf",
  status: "indexed",
  size_bytes: 145200,
  chunk_count: 42,
  created_at: "2026-09-01T10:00:00Z",
}

test.describe("Knowledge & RAG", () => {
  test.beforeEach(async ({ page }) => {
    await setAuthState(page)
    await setupConversationMocks(page).setup()
  })

  test("lists indexed documents", async ({ page }) => {
    const mocks = mockApi(page)
    mocks.route(/\/api\/files(\?|$)/, (route) => {
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([FILE]) })
    })
    await mocks.setup()

    const app = new NexusAppPage(page)
    await app.goto()
    await page.getByText("Knowledge & RAG").click()

    await expect(page.getByText("Knowledge Base & RAG Index")).toBeVisible()
    await expect(page.getByText("nexus-spec.pdf")).toBeVisible()
    await expect(page.getByText("141.8 KB • 42 chunks")).toBeVisible()
    await expect(page.getByText("Indexed Documents (1)")).toBeVisible()
  })

  test("runs a semantic search against the RAG testbed", async ({ page }) => {
    const mocks = mockApi(page)
    mocks.route(/\/api\/files(\?|$)/, (route) => {
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([FILE]) })
    })
    mocks.route(/\/api\/files\/rag\/query/, (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          citations: [
            {
              filename: "nexus-spec.pdf",
              chunk_index: 4,
              score: 0.94,
              content_snippet: "Native Hybrid Search uses multi-stage prefetch.",
            },
          ],
        }),
      })
    })
    await mocks.setup()

    const app = new NexusAppPage(page)
    await app.goto()
    await page.getByText("Knowledge & RAG").click()

    await page
      .getByPlaceholder("Test a query against Qdrant (e.g., 'What is Hybrid Search?')")
      .fill("What is Hybrid Search?")
    await page.getByRole("button", { name: "Search" }).click()

    await expect(page.getByText("Native Hybrid Search uses multi-stage prefetch.")).toBeVisible()
    await expect(page.getByText(/Score: 0.94/)).toBeVisible()
  })

  test("deletes a document from the index", async ({ page }) => {
    const mocks = mockApi(page)
    mocks.route(/\/api\/files(\?|$)/, (route) => {
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([FILE]) })
    })
    mocks.route(/\/api\/files\/file-1(\?|$)/, (route) => {
      route.fulfill({ status: 204 })
    })
    await mocks.setup()

    const app = new NexusAppPage(page)
    await app.goto()
    await page.getByText("Knowledge & RAG").click()

    await expect(page.getByText("nexus-spec.pdf")).toBeVisible()
    await page.getByTitle("Delete document").click()

    await expect(page.getByText("nexus-spec.pdf")).not.toBeVisible()
    await expect(page.getByText("No documents indexed yet. Upload one above.")).toBeVisible()
  })

  test("uploads a document into the index", async ({ page }) => {
    const mocks = mockApi(page)
    mocks.route(/\/api\/files(\?|$)/, (route) => {
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([FILE]) })
    })
    mocks.route(/\/api\/files\/upload/, (route) => {
      route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: "file-2",
          filename: "spec.md",
          status: "indexed",
          size_bytes: 5120,
          chunk_count: 3,
          created_at: "2026-09-06T10:00:00Z",
        }),
      })
    })
    await mocks.setup()

    const app = new NexusAppPage(page)
    await app.goto()
    await page.getByText("Knowledge & RAG").click()

    await page.setInputFiles("input[type=file]", {
      name: "spec.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("# Spec"),
    })

    await expect(page.getByText("spec.md")).toBeVisible()
    await expect(page.getByText("Indexed Documents (2)")).toBeVisible()
  })

  test("falls back to sample documents when the index is unavailable", async ({ page }) => {
    const mocks = mockApi(page)
    mocks.route(/\/api\/files(\?|$)/, (route) => {
      route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({}) })
    })
    await mocks.setup()

    const app = new NexusAppPage(page)
    await app.goto()
    await page.getByText("Knowledge & RAG").click()

    await expect(page.getByText("Nexus_Architecture_Master_Spec.pdf")).toBeVisible()
    await expect(page.getByText("Production_AI_Design_Patterns.md")).toBeVisible()
  })
})