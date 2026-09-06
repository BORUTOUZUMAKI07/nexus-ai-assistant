import { test, expect, NexusAppPage, setupConversationMocks, setupFilesMock, setupChatMock, setAuthState } from "./fixtures"

test.describe("Chat & Workspace", () => {
  test.beforeEach(async ({ page }) => {
    await setAuthState(page)
    await setupConversationMocks(page).setup()
  })

  test("sends a message and streams an assistant reply", async ({ page }) => {
    await setupChatMock(page).setup()
    const app = new NexusAppPage(page)
    await app.goto()

    await expect(page.getByText("Project kickoff")).toBeVisible()
    await app.sendMessage("Hello!")

    await expect(page.getByText("Hello from Nexus.")).toBeVisible()
    await expect(page.getByText("Hello!")).toBeVisible()
  })

  test("web toggle sends the chat in research mode", async ({ page }) => {
    const chat = setupChatMock(page)
    await chat.setup()
    const app = new NexusAppPage(page)
    await app.goto()

    await page.getByTitle("Firecrawl Live Web Search").click()
    await app.sendMessage("Search the live web")

    await expect(page.getByText("Hello from Nexus.")).toBeVisible()
    expect(chat.capturedChatRequests.length).toBeGreaterThan(0)
    expect(chat.capturedChatRequests[0].mode).toBe("research")
  })

  test("knowledge tab lists indexed documents", async ({ page }) => {
    await setupFilesMock(page).setup()
    const app = new NexusAppPage(page)
    await app.goto()

    await page.getByText("Knowledge & RAG").click()

    await expect(page.getByText("nexus-spec.pdf")).toBeVisible()
    await expect(page.getByText("Indexed Documents (1)")).toBeVisible()
  })

  test("settings tab shows the BYOK vault and memories", async ({ page }) => {
    const app = new NexusAppPage(page)
    await app.goto()

    await page.getByText("Settings & BYOK Keys").click()

    await expect(page.getByText("Bring Your Own Key (BYOK) Encryption Vault")).toBeVisible()
    await expect(page.getByText(/Persistent User Memories/)).toBeVisible()
  })
})