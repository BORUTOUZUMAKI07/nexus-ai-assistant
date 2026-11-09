import { test, expect } from "@playwright/test";

test.use({ storageState: { cookies: [], origins: [] } });

test.describe("Nexus AI Assistant E2E", () => {
  test("homepage loads and shows chat", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("text=Nexus AI")).toBeVisible();
  });

  test("sign-in page loads", async ({ page }) => {
    await page.goto("/signin");
    await expect(page.locator("text=Sign in")).toBeVisible();
  });

  test("keyboard shortcuts toggle with ?", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("?");
    await expect(page.getByText("Keyboard Shortcuts")).toBeVisible();
    await page.keyboard.press("?");
    await expect(page.getByText("Keyboard Shortcuts")).not.toBeVisible();
  });

  test("dark mode toggle works", async ({ page }) => {
    await page.goto("/");
    await page.getByTitle("Switch to dark mode").click();
    await expect(page.locator("html")).toHaveAttribute("class", /dark/);
  });
});
