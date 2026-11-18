import { defineConfig, devices } from "@playwright/test"

/**
 * The credential-backed lane. It deliberately has no `webServer`: it drives the
 * deployed app, so a local mock must never answer for it. No traces or videos
 * are retained because they can contain session cookies and filled credentials.
 */
const baseURL =
  process.env.E2E_LIVE_BASE_URL ?? "https://nexus-ai-assistant-azure.vercel.app"

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/live-auth.spec.ts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  reporter: "list",
  use: {
    baseURL,
    trace: "off",
    video: "off",
    screenshot: "off",
  },
  projects: [
    {
      name: "chromium-live",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
})
