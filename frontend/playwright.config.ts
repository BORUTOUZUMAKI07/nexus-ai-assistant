import { defineConfig, devices } from "@playwright/test"

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "html",
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    // Pin the port on the command line. `next dev` silently picks another port
    // when 3000 is busy (or when a stale server is still parked there), and
    // Playwright then polls the configured url until it times out at 120s — a
    // 2-minute failure that looks like a broken test run and is really a port
    // collision. With -p 3000 Next fails fast instead of drifting.
    command: "npx next dev -p 3000",
    url: "http://localhost:3000",
    // NOT `reuseExistingServer: true` locally. A server already on :3000 is
    // assumed to be ours, but this machine also runs other projects on that
    // port — a run then drives a stranger's UI and reports on it as if it were
    // Nexus. Starting our own server makes a busy port a fast, honest failure
    // ("port 3000 is in use") instead of a green run against the wrong app.
    reuseExistingServer: false,
    timeout: 120000,
  },
})