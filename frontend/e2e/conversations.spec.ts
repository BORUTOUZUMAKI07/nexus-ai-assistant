import { test, expect, NexusAppPage, mockApi, setAuthState, TEST_CONVERSATION } from "./fixtures"

test.describe("Conversation management", () => {
  test.beforeEach(async ({ page }) => {
    await setAuthState(page)
  })

  test("starts a new conversation with the backend id", async ({ page }) => {
    const mocks = mockApi(page)
    mocks.route(/\/api\/conversations(\?|$)/, (route) => {
      if (route.request().method() === "POST") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ...TEST_CONVERSATION, id: "new-1", title: "Brand new chat" }),
        })
      }
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([TEST_CONVERSATION]),
      })
    })
    await mocks.setup()

    const app = new NexusAppPage(page)
    await app.goto()
    await expect(page.getByText("Project kickoff")).toBeVisible()

    await page.getByRole("button", { name: /New Conversation/ }).click()

    await expect(page.getByText("Brand new chat")).toBeVisible()
  })

  test("deletes a conversation and keeps the remaining one", async ({ page }) => {
    const second = { ...TEST_CONVERSATION, id: "conv-101", title: "Security review" }
    const mocks = mockApi(page)
    mocks.route(/\/api\/conversations(\?|$)/, (route) => {
      if (route.request().method() === "POST") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(TEST_CONVERSATION),
        })
      }
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([TEST_CONVERSATION, second]),
      })
    })
    mocks.route(/\/api\/conversations\/conv-101(\?|$)/, (route) => {
      route.fulfill({ status: 204 })
    })
    await mocks.setup()

    const app = new NexusAppPage(page)
    await app.goto()
    await expect(page.getByText("Security review")).toBeVisible()

    await page
      .locator("div.group")
      .filter({ hasText: "Security review" })
      .getByRole("button")
      .click()

    await expect(page.getByText("Security review")).not.toBeVisible()
    await expect(page.getByText("Project kickoff")).toBeVisible()
  })
})