import {
  test,
  expect,
  openConversation,
  historyTranscript,
  setAuthState,
  HISTORY_USER_ID,
  HISTORY_ASSISTANT_ID,
  HISTORY_QUESTION,
  HISTORY_ANSWER,
} from "./fixtures"

/**
 * Message-level actions: edit-and-resend, regenerate, the citation inspector
 * and the artifact canvas.
 *
 * These four were listed in docs/golden-paths.md as "manual only" — unit tests
 * covered the hooks, but nothing proved the button a user clicks reaches the
 * right endpoint and changes what is on screen. The assertions below are the
 * observable end state, not the click.
 */
test.describe("Message actions", () => {
  test.beforeEach(async ({ page }) => {
    await setAuthState(page)
  })

  test("edits a user turn and resends the edited text", async ({ page }) => {
    await openConversation(page, historyTranscript())

    await page.getByText(HISTORY_QUESTION).first().hover()
    await page
      .getByTitle("Edit & resend (discards later turns)")
      .first()
      .click()

    const editor = page.getByLabel("Edit message")
    await expect(editor).toBeVisible()
    await editor.fill("what is the capital of Spain")

    const patched = page.waitForRequest(
      (r) =>
        r.method() === "PATCH" && r.url().includes(`/messages/${HISTORY_USER_ID}`)
    )
    // sendMessage targets /api/chat and may carry ?conversationId= — hence
    // `includes`, not `endsWith`.
    const resent = page.waitForRequest(
      (r) => r.method() === "POST" && r.url().includes("/api/chat")
    )

    await page.getByRole("button", { name: "Save & resend" }).click()

    const patchBody = (await (await patched).postDataJSON()) as { content?: string }
    expect(patchBody.content).toBe("what is the capital of Spain")

    // The edited text is what the next turn asks about — the whole point of the
    // action, and invisible in the PATCH assertion alone.
    expect((await resent).postData() ?? "").toContain("Spain")
    await expect(page.getByText("A fresh answer.")).toBeVisible()
  })

  test("regenerates the last answer through the backend prompt", async ({ page }) => {
    const mocks = await openConversation(page, historyTranscript())

    mocks.route(/\/regenerate$/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          prompt: HISTORY_QUESTION,
          user_message_id: HISTORY_USER_ID,
        }),
      })
    )

    const regenerate = page.waitForRequest(
      (r) => r.url().includes("/regenerate") && r.method() === "POST"
    )

    await page.getByText(HISTORY_ANSWER).first().hover()
    await page.getByTitle("Regenerate answer").first().click()

    // The route is addressed by the *assistant* message: regenerating is "this
    // answer is stale", and the backend decides which prompt to re-ask from the
    // stored row rather than from anything the client sends.
    expect((await regenerate).url()).toContain(`/messages/${HISTORY_ASSISTANT_ID}/regenerate`)
    await expect(page.getByText("A fresh answer.")).toBeVisible()
  })

  test("opens the citation inspector with the grounded passage", async ({ page }) => {
    const snippet = "Paris is the capital and largest city of France."
    await openConversation(
      page,
      historyTranscript({
        citations: [
          {
            filename: "geo-notes.pdf",
            chunk_index: 3,
            score: 0.91,
            content_snippet: snippet,
          },
        ],
      })
    )

    await page.getByTitle("Inspect source").first().click()

    const inspector = page.getByRole("dialog", { name: "Grounding Source" })
    await expect(inspector).toBeVisible()
    await expect(inspector.getByText(snippet)).toBeVisible()
    await expect(inspector.getByText("geo-notes.pdf")).toBeVisible()
    await expect(inspector.getByText("91% Match")).toBeVisible()

    await page.getByLabel("Close citation inspector").click()
    await expect(inspector).toBeHidden()
  })

  test("saves an answer as an artifact, edits it and writes a new version", async ({
    page,
  }) => {
    const code = "def greet(name):\n    return f'hi {name}'\n\nprint(greet('nexus'))"
    const mocks = await openConversation(
      page,
      historyTranscript({ content: `Here is the snippet:\n\n\`\`\`python\n${code}\n\`\`\`` })
    )

    // The canvas only offers editing for a *persisted* artifact (a live one has
    // no server id to version), so the journey starts by saving it.
    let version = 1
    mocks.route(/\/api\/artifacts$/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ id: "art-1", title: "PYTHON Snippet", version }),
      })
    )
    mocks.route(/\/api\/artifacts\/art-1\/versions$/, (route) => {
      version += 1
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ id: "art-1", title: "PYTHON Snippet", version }),
      })
    })
    mocks.route(/\/api\/artifacts(\?|$)/, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
    )

    await expect(page.getByText("PYTHON Snippet").first()).toBeVisible()

    const saved = page.waitForRequest(
      (r) => r.method() === "POST" && /\/api\/artifacts$/.test(r.url())
    )
    await page.getByTitle("Save artifact (persisted + versioned)").first().click()
    await saved

    // "Open in Canvas" after a save must open the *persisted* document; opening
    // the transient copy is what made the canvas read-only here.
    await page.getByRole("button", { name: /Open in Canvas/ }).first().click()
    await expect(page.getByLabel("Edit and save a new version")).toBeVisible()

    await page.getByLabel("Edit and save a new version").click()
    const editor = page.getByLabel("Edit PYTHON Snippet")
    await expect(editor).toBeVisible()
    await editor.fill(`${code}\nprint(greet('again'))`)

    const newVersion = page.waitForRequest(
      (r) => r.method() === "POST" && r.url().includes("/artifacts/art-1/versions")
    )
    await page.getByRole("button", { name: "Save new version" }).click()

    expect((await newVersion).postData() ?? "").toContain("again")
    await expect(page.getByLabel("Edit PYTHON Snippet")).toBeHidden()

    await page.getByTitle("Close Canvas").click()
    await expect(page.getByTitle("Close Canvas")).toBeHidden()
  })
})
