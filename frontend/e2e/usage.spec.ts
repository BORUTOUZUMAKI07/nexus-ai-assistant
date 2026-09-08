import { test, expect, NexusAppPage, setupConversationMocks, setAuthState, mockApi } from "./fixtures"

const SUMMARY = {
  total_tokens: 250000,
  prompt_tokens: 190000,
  completion_tokens: 60000,
  cached_tokens: 0,
  total_cost_usd: 1.25,
  average_latency_ms: 320,
  total_requests: 900,
}

test.describe("Usage panel", () => {
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
        body: JSON.stringify(SUMMARY),
      })
    })
    await mocks.setup()

    const app = new NexusAppPage(page)
    await app.goto()
    await page.getByRole("button", { name: "Usage", exact: true }).click()

    await expect(page.getByText("Token consumption and cost across your sessions.")).toBeVisible()
    await expect(page.getByText("Total tokens")).toBeVisible()
    await expect(page.getByText("250,000")).toBeVisible()
    await expect(page.getByText("Estimated cost")).toBeVisible()
    await expect(page.getByText("$1.2500")).toBeVisible()
    await expect(page.getByText("Avg latency")).toBeVisible()
    await expect(page.getByText("320ms")).toBeVisible()
    await expect(page.getByText("Requests")).toBeVisible()
  })

  test("renders the input / output token breakdown", async ({ page }) => {
    const mocks = mockApi(page)
    mocks.route(/\/api\/usage\/summary/, (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(SUMMARY),
      })
    })
    await mocks.setup()

    const app = new NexusAppPage(page)
    await app.goto()
    await page.getByRole("button", { name: "Usage", exact: true }).click()

    await expect(page.getByText("Input tokens")).toBeVisible()
    await expect(page.getByText("190,000")).toBeVisible()
    await expect(page.getByText("Output tokens")).toBeVisible()
    await expect(page.getByText("60,000")).toBeVisible()
    await expect(page.getByText("Cached tokens")).toBeVisible()
  })

  test("reflects real backend stats when available", async ({ page }) => {
    const mocks = mockApi(page)
    mocks.route(/\/api\/usage\/summary/, (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          total_tokens: 42,
          prompt_tokens: 20,
          completion_tokens: 22,
          cached_tokens: 2,
          total_cost_usd: 0,
          average_latency_ms: 118,
          total_requests: 3,
        }),
      })
    })
    await mocks.setup()

    const app = new NexusAppPage(page)
    await app.goto()
    await page.getByRole("button", { name: "Usage", exact: true }).click()

    await expect(page.getByText("42", { exact: true })).toBeVisible()
    await expect(page.getByText("$0.0000")).toBeVisible()
    await expect(page.getByText("118ms")).toBeVisible()
  })
})