import { expect, test } from "@playwright/test"

/**
 * Real authentication against the deployed app. No routes are mocked here: a
 * green result means the actual login endpoint, cookie, session probe, and
 * sign-out all worked for a real account.
 *
 * Run with:
 *   E2E_TEST_EMAIL=... E2E_TEST_PASSWORD=... npm run test:e2e:live
 */
const email = process.env.E2E_TEST_EMAIL
const password = process.env.E2E_TEST_PASSWORD

test.describe("live authentication", () => {
  test.skip(
    !email || !password,
    "set E2E_TEST_EMAIL and E2E_TEST_PASSWORD to run the live auth lane"
  )

  test("a real account signs in, reaches /app, and signs out", async ({ page }) => {
    if (!email || !password) throw new Error("live auth credentials are missing")

    const pageErrors: string[] = []
    page.on("pageerror", (error) => pageErrors.push(error.message))

    await page.goto("/signin")
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible()

    await page.getByPlaceholder("name@example.com").fill(email)
    await page.getByPlaceholder("••••••••").fill(password)
    await page.getByRole("button", { name: /Sign In/i }).click()

    await expect(page).toHaveURL(/\/app/, { timeout: 30_000 })
    await expect(page.getByRole("heading", { name: "Nexus AI" })).toBeVisible()

    const session = await page.request.get("/api/auth/me")
    expect(session.ok(), `auth probe returned ${session.status()}`).toBeTruthy()
    const sessionBody = (await session.json()) as {
      authenticated: boolean
      user: { email: string }
    }
    expect(sessionBody.authenticated).toBe(true)
    expect(sessionBody.user.email.toLowerCase()).toBe(email.toLowerCase())

    await page.getByTitle("Sign out").click()
    await expect(page).toHaveURL(/\/signin$/)
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible()

    await page.goto("/")
    await expect(page.getByTestId("landing-ready")).toBeVisible()

    const cleared = await page.request.get("/api/auth/me")
    expect(cleared.ok(), `auth probe returned ${cleared.status()}`).toBeTruthy()
    expect((await cleared.json()).authenticated).toBe(false)
    expect(pageErrors).toEqual([])
  })
})
