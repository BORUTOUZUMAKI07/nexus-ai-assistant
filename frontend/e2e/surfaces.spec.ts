import {
  test,
  expect,
  mockApi,
  setAuthState,
  openConversation,
  historyTranscript,
  setupConversationMocks,
  setupFilesMock,
  setupSettingsMock,
  TEST_CONVERSATION,
  HISTORY_ANSWER,
} from "./fixtures"

/**
 * Surfaces that had no automated journey at all: plan mode, the public share
 * page, the command palette, organizations + webhooks, and the two audio paths
 * (voice input and read-aloud).
 *
 * Every assertion is an observable end state or an outbound request with a
 * body — "the button exists" is not a journey.
 */

test.describe("Plan mode", () => {
  test.beforeEach(async ({ page }) => {
    await setAuthState(page)
  })

  test("drafts a plan, and approving it sends the plan to the agent", async ({
    page,
  }) => {
    const mocks = await openConversation(page, historyTranscript())

    const plan = {
      id: "plan-1",
      conversation_id: TEST_CONVERSATION.id,
      user_id: "user-1",
      title: "Ship the release notes",
      summary: "Three steps, no code changes.",
      steps: ["Collect merged PRs", "Draft the notes", "Publish"],
      status: "pending",
      decision_reason: null,
      created_at: "2026-10-21T00:00:00Z",
      updated_at: "2026-10-21T00:00:00Z",
      decided_at: null,
    }

    // Drafting is POST .../plan (singular); history is GET .../plans (plural).
    mocks.route(/\/api\/conversations\/[^/?#]+\/plans?(\?|$)/, (route) => {
      if (route.request().method() === "POST") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(plan),
        })
      }
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
    })
    mocks.route(/\/api\/plans\/[^/?#]+\/approve$/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...plan, status: "approved" }),
      })
    )

    await page.getByRole("button", { name: "Plan" }).click()
    await expect(page.getByTitle(/Plan mode ON/)).toBeVisible()

    const drafted = page.waitForRequest(
      (r) => r.method() === "POST" && /\/conversations\/[^/?#]+\/plan$/.test(r.url())
    )
    await page
      .getByPlaceholder("Ask Nexus anything, write code, search live web...")
      .fill("write the release notes")
    await page.keyboard.press("Enter")
    await drafted

    await expect(page.getByText("Proposed Plan")).toBeVisible()
    await expect(page.getByText("Ship the release notes")).toBeVisible()
    await expect(page.getByText("Draft the notes")).toBeVisible()

    const approved = page.waitForRequest(
      (r) => r.method() === "POST" && r.url().includes("/plans/plan-1/approve")
    )
    const executed = page.waitForRequest(
      (r) => r.method() === "POST" && r.url().includes("/api/chat")
    )

    await page.getByRole("button", { name: /Approve & Execute/ }).click()
    await approved

    // Approval is not the end of the journey: the plan has to reach the model,
    // otherwise "execute" means nothing. The preamble carries the steps.
    const body = (await executed).postData() ?? ""
    expect(body).toContain("APPROVED PLAN")
    expect(body).toContain("Draft the notes")
    await expect(page.getByText("A fresh answer.")).toBeVisible()
  })
})

test.describe("Public share", () => {
  test("copies a share link built on the public app origin", async ({
    page,
    context,
  }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"])
    await setAuthState(page)

    const mocks = await openConversation(page, historyTranscript())
    const shareUrl = "https://nexus.example.com/share/tok-1"
    mocks.route(/\/api\/conversations\/[^/?#]+\/share$/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ token: "tok-1", url: shareUrl, expires_at: null }),
      })
    )

    const created = page.waitForRequest(
      (r) => r.method() === "POST" && r.url().includes("/share")
    )
    await page.getByTitle("Share conversation (read-only link)").first().click()
    await created

    await expect(page.getByText("Share link copied")).toBeVisible()
    const clipboard = await page.evaluate(() => navigator.clipboard.readText())
    // The host is the whole point: a link built on localhost is unusable in
    // production, which is what the APP_PUBLIC_URL guard exists to prevent.
    expect(clipboard).toBe(shareUrl)
  })

  test("renders a shared transcript signed out", async ({ page }) => {
    const mocks = mockApi(page)
    mocks.route(/\/api\/public\/shares\/tok-1$/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          conversation: {
            id: TEST_CONVERSATION.id,
            title: "Shared release review",
            created_at: "2026-10-21T00:00:00Z",
          },
          messages: [
            { role: "user", content: "what shipped?" },
            { role: "assistant", content: "**Three** fixes and a migration." },
          ],
        }),
      })
    )
    await mocks.setup()

    // No auth cookie, no /app shell: this is the page an anonymous reader gets.
    await page.goto("/share/tok-1")

    await expect(page.getByText("Read-only shared transcript")).toBeVisible()
    await expect(page.getByText("Shared release review")).toBeVisible()
    await expect(page.getByText("what shipped?")).toBeVisible()
    await expect(page.getByText("Three", { exact: false })).toBeVisible()
  })
})

test.describe("Command palette", () => {
  test.beforeEach(async ({ page }) => {
    await setAuthState(page)
    await setupConversationMocks(page).setup()
    await setupFilesMock(page).setup()
  })

  test("opens on Ctrl+K and navigates to Knowledge", async ({ page }) => {
    await page.goto("/app")
    await expect(
      page.getByRole("complementary").getByText(TEST_CONVERSATION.title)
    ).toBeVisible()

    await page.keyboard.press("Control+k")
    const search = page.getByPlaceholder("Search conversations, or type a command…")
    await expect(search).toBeVisible()

    await search.fill("Knowledge")
    await page.getByRole("option", { name: /Go to Knowledge/ }).click()

    // The palette must close when it acts, and the destination must actually
    // render — a palette that "navigates" to the same screen is a no-op.
    await expect(search).toBeHidden()
    await expect(page.getByPlaceholder("Filter documents…")).toBeVisible()
  })
})

test.describe("Organizations and webhooks", () => {
  test.beforeEach(async ({ page }) => {
    await setAuthState(page)
    await setupConversationMocks(page).setup()
    await setupSettingsMock(page).setup()
  })

  test("creates an org, invites a member, registers a webhook and sees a delivery", async ({
    page,
  }) => {
    const mocks = mockApi(page)

    mocks.route(/\/api\/orgs(\?|$)/, (route) => {
      if (route.request().method() === "POST") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ id: "org-1", name: "Acme", role: "owner" }),
        })
      }
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([{ id: "org-1", name: "Acme", role: "owner" }]),
      })
    })

    mocks.route(/\/api\/orgs\/org-1\/invites$/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          token: "inv-1",
          dev_accept_url: "https://nexus.example.com/accept/inv-1",
          expires_at: "2026-11-01T00:00:00Z",
        }),
      })
    )

    mocks.route(/\/api\/webhooks(\?|$)/, (route) => {
      if (route.request().method() === "POST") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            id: "wh-2",
            url: "https://example.com/hooks/nexus",
            events: ["run.completed"],
            is_active: true,
            has_secret: true,
            secret: "whsec_test_secret",
          }),
        })
      }
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "wh-1",
            url: "https://example.com/hooks/nexus",
            events: ["run.completed"],
            is_active: true,
            has_secret: true,
          },
        ]),
      })
    })

    mocks.route(/\/api\/webhooks\/wh-1\/deliveries(\?|$)/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "d-1",
            event: "run.completed",
            status: "failed",
            http_status: 500,
            attempts: 3,
            created_at: "2026-10-21T10:00:00Z",
          },
        ]),
      })
    )

    await mocks.setup()
    await page.goto("/app")
    await page.getByRole("button", { name: "Settings" }).click()

    const orgs = page.getByRole("region", { name: "Organizations" })
    await expect(orgs).toBeVisible()
    await expect(orgs.getByText("Acme")).toBeVisible()

    const created = page.waitForRequest(
      (r) => r.method() === "POST" && /\/api\/orgs$/.test(r.url())
    )
    await page.getByLabel("New organization name").fill("Acme Labs")
    await page.getByRole("button", { name: /Create/ }).click()
    expect((await created).postData() ?? "").toContain("Acme Labs")

    await orgs.getByRole("button", { name: "Invite" }).click()
    await page.getByLabel("Invitee email").fill("dev@example.com")
    await orgs.getByRole("button", { name: "Create invite" }).click()
    await expect(
      page.getByText("https://nexus.example.com/accept/inv-1")
    ).toBeVisible()

    const hooks = page.getByRole("region", { name: "Webhooks" })
    await expect(hooks.getByText("https://example.com/hooks/nexus")).toBeVisible()

    await page.getByLabel("Webhook URL").fill("https://example.com/hooks/nexus")
    await page.getByRole("button", { name: /Add endpoint/ }).click()
    // The signing secret is returned once; losing it is unrecoverable, so the
    // journey ends with it visible rather than only "registered".
    await expect(page.getByText("whsec_test_secret")).toBeVisible()

    await page.getByTitle("Show delivery history").first().click()
    // Cell-scoped: the endpoint row above the table also prints the event name.
    await expect(page.getByRole("cell", { name: "run.completed" })).toBeVisible()
    await expect(page.getByRole("cell", { name: "failed" })).toBeVisible()
    await expect(page.getByRole("cell", { name: "500" })).toBeVisible()
  })
})

test.describe("Audio", () => {
  test.beforeEach(async ({ page }) => {
    await setAuthState(page)
  })

  test("dictates a message through the transcriber", async ({ page }) => {
    const mocks = mockApi(page)
    await setupConversationMocks(page).setup()
    mocks.route(/\/api\/audio\/transcribe$/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ text: "summarise the roadmap" }),
      })
    )
    await mocks.setup()

    // A silent fake capture never reaches the transcriber: the composer skips
    // clips under 700 ms / 1 KB, because a silent clip is exactly the input that
    // makes Whisper hallucinate. This test used to click stop the instant
    // recording started, so the guard (added later) silently swallowed the
    // request. The double below emits a clip that clears the size floor and the
    // wait clears the duration floor -- the assertion still proves the real
    // client path (record -> guard passes -> transcribe -> transcript),
    // it just supplies input worth transcribing instead of nothing.
    await page.addInitScript(() => {
      class FakeMediaRecorder {
        static isTypeSupported = () => true
        ondataavailable: ((event: { data: Blob }) => void) | null = null
        onstop: (() => void) | null = null
        constructor(public stream: MediaStream) {}
        start() {}
        stop() {
          this.ondataavailable?.({
            data: new Blob([new Uint8Array(8192)], { type: "audio/webm" }),
          })
          this.onstop?.()
        }
      }
      Object.defineProperty(window, "MediaRecorder", {
        configurable: true,
        writable: true,
        value: FakeMediaRecorder,
      })
    })

    await page.goto("/app")

    await page.getByTitle("Voice input (Groq Whisper)").click()
    await expect(page.getByTitle("Stop recording")).toBeVisible()

    const transcribed = page.waitForRequest(
      (r) => r.method() === "POST" && r.url().includes("/api/audio/transcribe")
    )
    await page.waitForTimeout(900)
    await page.getByTitle("Stop recording").click()
    await transcribed

    // The transcript has to land in the composer, not in a toast.
    await expect(
      page.getByPlaceholder("Ask Nexus anything, write code, search live web...")
    ).toHaveValue("summarise the roadmap")
  })

  test("reads an answer aloud through the speech endpoint", async ({ page }) => {
    const mocks = await openConversation(page, historyTranscript())

    // A minimal valid WAV header, so `new Audio(...).play()` has something real
    // to decode instead of exercising the browser fallback path.
    const wav = Buffer.from(
      "UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=",
      "base64"
    )
    mocks.route(/\/api\/audio\/speech/, (route) =>
      route.fulfill({ status: 200, contentType: "audio/wav", body: wav })
    )

    const spoken = page.waitForRequest(
      (r) => r.url().includes("/api/audio/speech") && r.method() === "GET"
    )

    await page.getByText(HISTORY_ANSWER).first().hover()
    await page.getByTitle("Read aloud (TTS)").first().click()

    const request = await spoken
    expect(request.url()).toContain("text=Paris")
  })
})
