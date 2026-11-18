import { test, expect, NexusAppPage, setupMockAuthRoutes, setupConversationMocks, setMockAuthState } from "./fixtures"

test.describe("Authentication", () => {
  test("anonymous visitor sees the login gate", async ({ page }) => {
    await setupConversationMocks(page).setup()
    const app = new NexusAppPage(page)
    await app.goto()

    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible()
  })

  test("valid credentials enter the app and load conversations", async ({ page }) => {
    const auth = setupMockAuthRoutes(page)
    await auth.setup()
    await setupConversationMocks(page).setup()

    const app = new NexusAppPage(page)
    await app.goto()
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible()

    await app.login("test@nexus.ai", "password123")

    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeHidden({ timeout: 10000 })
    await expect(page.getByRole("complementary").getByText("Project kickoff")).toBeVisible()
    await expect(page.getByRole("heading", { name: "Nexus AI" })).toBeVisible()
  })

  test("invalid credentials show a readable error", async ({ page }) => {
    const auth = setupMockAuthRoutes(page)
    await auth.setup()

    const app = new NexusAppPage(page)
    await app.goto()
    await app.login("test@nexus.ai", "wrong-password")

    await expect(page.getByText("Incorrect email or password.")).toBeVisible()
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible()
  })

  test("signing out clears the session and returns to the landing page", async ({ page }) => {
    await setMockAuthState(page)
    await setupConversationMocks(page).setup()

    const app = new NexusAppPage(page)
    await app.goto()

    await expect(page.getByRole("complementary").getByText("Project kickoff")).toBeVisible()
    await page.getByTitle("Sign out").click()

    // A completed sign-out lands on the sign-in page, not the marketing page.
    await page.waitForURL(/\/signin$/)
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible()

    // The public landing page must still be reachable with the session gone.
    await page.goto("/")
    await expect(page.getByTestId("landing-ready")).toBeVisible()
    await expect(
      page.getByRole("heading", { name: /An AI that researches, reasons, and/ })
    ).toBeVisible()
  })

  test("landing page links to the sign-in page", async ({ page }) => {
    await page.goto("/")
    await expect(page.getByTestId("landing-ready")).toBeVisible()
    await page.getByRole("link", { name: "Sign in" }).click()
    await expect(page).toHaveURL(/\/signin/)
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible()
  })

  test("sign-in page login enters the app", async ({ page }) => {
    const auth = setupMockAuthRoutes(page)
    await auth.setup()
    await setupConversationMocks(page).setup()

    await page.goto("/signin")
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible()

    const app = new NexusAppPage(page)
    await app.login("test@nexus.ai", "password123")

    await page.waitForURL(/\/app/)
    await expect(page.getByRole("complementary").getByText("Project kickoff")).toBeVisible({ timeout: 10000 })
  })

  test("sign-in page redirects to the app when already authenticated", async ({ page }) => {
    // The redirect hinges on the /api/auth/me probe reporting a real session,
    // so the auth gate must be mocked (the access cookie is not a real JWT).
    const auth = setupMockAuthRoutes(page)
    await auth.setup()
    await setupConversationMocks(page).setup()
    await setMockAuthState(page)

    await page.goto("/signin")
    await page.waitForURL(/\/app/)
  })
})