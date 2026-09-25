import { test, expect, NexusAppPage, setupConversationMocks, setAuthState, mockApi, ADMIN_TOKEN } from "./fixtures"
import type { Page } from "@playwright/test"

const USERS = [
  {
    id: "usr-01",
    email: "admin@nexus.ai",
    username: "admin",
    full_name: "Lead Administrator",
    role: "admin",
    is_active: true,
    created_at: "2026-09-01T10:00:00Z",
  },
  {
    id: "usr-02",
    email: "dev@nexus.ai",
    username: "dev_user",
    full_name: "Staff Engineer",
    role: "user",
    is_active: false,
    created_at: "2026-09-02T10:00:00Z",
  },
]

test.describe("Admin Center", () => {
  test.beforeEach(async ({ page }) => {
    await setAuthState(page, ADMIN_TOKEN)
    await setupConversationMocks(page).setup()
  })

  async function openAdmin(page: Page) {
    const app = new NexusAppPage(page)
    await app.goto()
    await page.getByRole("button", { name: "Admin", exact: true }).click()
    await expect(page.getByText("Users, system health, and security audit trails")).toBeVisible()
  }

  test("lists the user directory with roles and status", async ({ page }) => {
    const mocks = mockApi(page)
    mocks.route(/\/api\/admin\/users(\?|$)/, (route) => {
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(USERS) })
    })
    await mocks.setup()
    await openAdmin(page)

    await expect(page.getByText("admin@nexus.ai")).toBeVisible()
    await expect(page.getByText("dev@nexus.ai")).toBeVisible()
    await expect(page.getByText("Lead Administrator")).toBeVisible()
    await expect(page.getByText("Staff Engineer")).toBeVisible()
    await expect(page.getByText("admin", { exact: true })).toBeVisible()
    await expect(page.getByText("user", { exact: true })).toBeVisible()
    await expect(page.getByText("Active")).toBeVisible()
    await expect(page.getByText("Disabled")).toBeVisible()
  })

  test("disables and re-enables a user", async ({ page }) => {
    const mocks = mockApi(page)
    mocks.route(/\/api\/admin\/users(\?|$)/, (route) => {
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(USERS) })
    })
    mocks.route(/\/api\/admin\/users\/usr-01\/toggle-status/, (route) => {
      route.fulfill({ status: 200, contentType: "application/json", body: "{}" })
    })
    await mocks.setup()
    await openAdmin(page)

    const adminRow = page.getByRole("row").filter({ hasText: "admin@nexus.ai" })
    await adminRow.getByRole("button", { name: "Disable" }).click()
    await expect(adminRow.getByText("Disabled")).toBeVisible()

    await adminRow.getByRole("button", { name: "Enable" }).click()
    await expect(adminRow.getByText("Active")).toBeVisible()
  })

  test("shows system health cards", async ({ page }) => {
    const mocks = mockApi(page)
    mocks.route(/\/api\/admin\/system-status/, (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "healthy", database: "connected", redis_cache: "connected" }),
      })
    })
    await mocks.setup()
    await openAdmin(page)

    await page.getByRole("button", { name: "System health" }).click()
    await expect(page.getByText("PostgreSQL")).toBeVisible()
    await expect(page.getByText("Redis Cache")).toBeVisible()
    await expect(page.getByText("API status")).toBeVisible()
    await expect(page.getByText("connected", { exact: true })).toHaveCount(2)
  })

  test("renders compliance audit logs", async ({ page }) => {
    const mocks = mockApi(page)
    mocks.route(/\/api\/admin\/audit-logs/, (route) => {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "log-1",
            action: "AUTH_LOGIN",
            resource_type: "user",
            status: "success",
            ip_address: "127.0.0.1",
            created_at: "2026-09-06T09:00:00Z",
          },
          {
            id: "log-2",
            action: "FILE_UPLOAD",
            resource_type: "rag_index",
            status: "success",
            ip_address: "127.0.0.1",
            created_at: "2026-09-06T08:00:00Z",
          },
        ]),
      })
    })
    await mocks.setup()
    await openAdmin(page)

    await page.getByRole("button", { name: "Audit logs" }).click()
    await expect(page.getByText("AUTH_LOGIN")).toBeVisible()
    await expect(page.getByText("FILE_UPLOAD")).toBeVisible()
    await expect(page.getByText("rag_index")).toBeVisible()
    await expect(page.getByText("127.0.0.1")).toHaveCount(2)
  })
})