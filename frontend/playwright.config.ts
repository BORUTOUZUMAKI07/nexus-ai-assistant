import { defineConfig, devices } from "@playwright/test"

export default defineConfig({
  testDir: "./e2e",
  // The credential-backed live auth lane is run explicitly with
  // `npm run test:e2e:live`; it must not join the local mocked suite.
  testIgnore: ["**/live-auth.spec.ts"],
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "html",
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
    // Voice input is a shipped path, so it belongs in the suite rather than in
    // the "needs a human with a microphone" list. Chromium's fake capture device
    // gives MediaRecorder real (silent) chunks, and the transcriber itself is
    // mocked at the BFF boundary.
    permissions: ["microphone"],
    launchOptions: {
      args: [
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
      ],
    },
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    // Drive the standalone production build, the same artifact the container
    // and Vercel serve. `next dev` injects a `nextjs-portal` overlay that sits
    // on top of buttons, so a deterministic UI suite either flakes or "fixes"
    // itself by clicking through development chrome that users never see.
    // The build takes longer than a dev boot, hence the five-minute budget.
    command: "npm run build && npm run start",
    url: "http://localhost:3000",
    // NOT `reuseExistingServer: true` locally. A server already on :3000 is
    // assumed to be ours, but this machine also runs other projects on that
    // port — a run then drives a stranger's UI and reports on it as if it were
    // Nexus. Starting our own server makes a busy port a fast, honest failure
    // ("port 3000 is in use") instead of a green run against the wrong app.
    reuseExistingServer: false,
    timeout: 300000,
  },
})