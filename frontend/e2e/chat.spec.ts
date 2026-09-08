import { test, expect, NexusAppPage, setupConversationMocks, setupFilesMock, setupSettingsMock, setupChatMock, setAuthState } from "./fixtures"

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

    await page.getByTitle("Live web search").click()
    await app.sendMessage("Search the live web")

    await expect(page.getByText("Hello from Nexus.")).toBeVisible()
    expect(chat.capturedChatRequests.length).toBeGreaterThan(0)
    expect(chat.capturedChatRequests[0].mode).toBe("research")
  })

  test("knowledge tab lists indexed documents", async ({ page }) => {
    await setupFilesMock(page).setup()
    const app = new NexusAppPage(page)
    await app.goto()

    await page.getByRole("button", { name: "Knowledge", exact: true }).click()

    await expect(page.getByText("Knowledge base")).toBeVisible()
    await expect(page.getByText("nexus-spec.pdf")).toBeVisible()
    await expect(page.getByText("Indexed documents")).toBeVisible()
  })

  test("settings tab shows BYOK providers and persistent memories", async ({ page }) => {
    await setupSettingsMock(page).setup()
    const app = new NexusAppPage(page)
    await app.goto()

    await page.getByRole("button", { name: "Settings", exact: true }).click()

    await expect(page.getByRole("heading", { name: "Bring-Your-Own-Key providers" })).toBeVisible()
    await expect(page.getByRole("heading", { name: "Persistent memories" })).toBeVisible()
  })
})