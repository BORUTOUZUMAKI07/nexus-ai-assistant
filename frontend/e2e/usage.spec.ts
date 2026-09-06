import { test, expect, NexusAppPage, setupConversationMocks, setAuthState, mockApi } from "./fixtures"

test.describe("Usage & Free Tier Budget", () => {
  test.beforeEach(async ({ page }) => {
    await setAuthState(page)
    await setupConversationMocks(page).setup()
  })

  test("shows backend-driven usage metric cards", async ({ page }) => {
    const mocks = mockApi(page)
    mocks.route(/\/api\/usage\/summary/, (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          total_tokens: 250000,
          input_tokens: 190000,
          output_tokens: 60000,
          total_cost_usd: 1.25,
          request_count: 900,
          period: "30d",
        }),
      })
    })
    await mocks.setup()

    const app = new NexusAppPage(page)
    await app.goto()
    await page.getByText("Usage & Free Tier Budget").click()

    await expect(page.getByText("Usage & Zero-Cost Telemetry")).toBeVisible()
    await expect(page.getByText("250,000")).toBeVisible()
    await expect(page.getByText("$1.25")).toBeVisible()
    await expect(page.getByText("Total Tokens")).toBeVisible()
    await expect(page.getByText("Total Cost")).toBeVisible()
  })

  test("renders provider free-tier quota monitors", async ({ page }) => {
    const app = new NexusAppPage(page)
    await app.goto()
    await page.getByText("Usage & Free Tier Budget").click()

    await expect(page.getByText("Provider Free Tier Quota Monitors")).toBeVisible()
    await expect(page.getByText("Groq (Llama 3.3 70B)")).toBeVisible()
    await expect(page.getByText("Qdrant Cloud (Hybrid Vector DB)")).toBeVisible()
    await expect(page.getByText("E2B Sandbox (Code Interpreter)")).toBeVisible()
  })

  test("reflects real backend stats when available", async ({ page }) => {
    const mocks = mockApi(page)
    mocks.route(/\/api\/usage\/summary/, (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          total_tokens: 42,
          input_tokens: 20,
          output_tokens: 22,
          total_cost_usd: 0,
          request_count: 3,
          period: "7d",
        }),
      })
    })
    await mocks.setup()

    const app = new NexusAppPage(page)
    await app.goto()
    await page.getByText("Usage & Free Tier Budget").click()

    await expect(page.getByText("42", { exact: true })).toBeVisible()
    await expect(page.getByText("$0.00")).toBeVisible()
  })
})