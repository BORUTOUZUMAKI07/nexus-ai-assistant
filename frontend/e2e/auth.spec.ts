import { test, expect, NexusAppPage, setupAuthMocks, setupConversationMocks, setAuthState } from "./fixtures"

test.describe("Authentication", () => {
  test("anonymous visitor sees the login gate", async ({ page }) => {
    await setupConversationMocks(page).setup()
    const app = new NexusAppPage(page)
    await app.goto()

    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible()
  })

  test("valid credentials enter the app and load conversations", async ({ page }) => {
    const auth = setupAuthMocks(page)
    await auth.setup()
    await setupConversationMocks(page).setup()

    const app = new NexusAppPage(page)
    await app.goto()
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible()

    await app.login("test@nexus.ai", "password123")

    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeHidden({ timeout: 10000 })
    await expect(page.getByText("Project kickoff")).toBeVisible()
    await expect(page.getByText("NEXUS AI")).toBeVisible()
  })

  test("invalid credentials show a readable error", async ({ page }) => {
    const auth = setupAuthMocks(page)
    await auth.setup()

    const app = new NexusAppPage(page)
    await app.goto()
    await app.login("test@nexus.ai", "wrong-password")

    await expect(page.getByText("Incorrect email or password.")).toBeVisible()
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible()
  })

  test("signing out returns to the login gate", async ({ page }) => {
    await setAuthState(page)
    await setupConversationMocks(page).setup()

    const app = new NexusAppPage(page)
    await app.goto()

    await expect(page.getByText("Project kickoff")).toBeVisible()
    await page.getByTitle("Sign out").click()

    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible()
  })
})