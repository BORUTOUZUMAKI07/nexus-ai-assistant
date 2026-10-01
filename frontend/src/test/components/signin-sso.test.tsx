import { describe, it, expect, beforeEach, vi } from "vitest"
import { act, fireEvent, render, screen, waitFor } from "@/test/test-utils"
import SignInPage from "@/app/signin/page"

const { routerMock } = vi.hoisted(() => ({
  routerMock: { replace: vi.fn(), push: vi.fn() },
}))

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
}))

describe("Sign-in page SSO", () => {
  const assign = vi.fn()

  beforeEach(() => {
    routerMock.replace.mockClear()
    assign.mockReset()
    // jsdom forbids real navigation; capture every assign() the button issues.
    Object.defineProperty(window, "location", {
      value: { ...window.location, assign },
      configurable: true,
      writable: true,
    })
  })

  it("sends the browser to the provider authorize URL when Google is clicked", async () => {
    render(<SignInPage />)

    fireEvent.click(
      await screen.findByRole("button", { name: /continue with google/i }),
    )

    await waitFor(() => expect(assign).toHaveBeenCalledTimes(1))
    const url = assign.mock.calls[0][0] as string
    expect(url.startsWith("https://idp.example/authorize?")).toBe(true)
    // The PKCE params come straight from the backend proxy handler.
    expect(url).toContain("state=test-sso-state")
    expect(url).toContain("code_challenge_method=S256")
  })

  it("sends the browser to the provider authorize URL when GitHub is clicked", async () => {
    render(<SignInPage />)

    fireEvent.click(
      await screen.findByRole("button", { name: /continue with github/i }),
    )

    await waitFor(() => expect(assign).toHaveBeenCalledTimes(1))
    expect(assign.mock.calls[0][0] as string).toContain("code_challenge_method=S256")
  })

  it("surfaces an inline error when SSO is unconfigured", async () => {
    // Override the MSW handler for this test: 404 = backend has no OAuth config.
    const { http, HttpResponse } = await import("msw")
    const { server } = await import("@/test/mocks/server")
    server.use(
      // The literal path has to be re-declared above the catch-all. A
      // `:provider` override alone also captures `/providers` — `:provider`
      // matches the string "providers" — so the page would fetch a 404, treat
      // it as "no SSO configured", and render no buttons at all. Same ordering
      // rule as the app, for the same reason.
      http.get("/api/auth/oauth/providers", () =>
        HttpResponse.json({
          providers: [
            { name: "google", configured: true },
            { name: "github", configured: true },
          ],
        }),
      ),
      http.get("/api/auth/oauth/:provider", () =>
        HttpResponse.json(
          { detail: "OAuth single sign-on is not configured." },
          { status: 404 },
        ),
      ),
    )

    render(<SignInPage />)
    fireEvent.click(
      await screen.findByRole("button", { name: /continue with google/i }),
    )

    await waitFor(() =>
      expect(
        screen.getByText(/single sign-on is not configured/i),
      ).toBeInTheDocument(),
    )
    expect(assign).not.toHaveBeenCalled()
  })
})

// ── which buttons exist ──────────────────────────────────────────────────────
//
// The page used to hardcode two buttons. That is wrong in both directions: a
// deployment with only GitHub configured still offered Google, and one with
// neither still offered both — the user found out after a round trip to an IdP.

describe("Sign-in page provider availability", () => {
  const assign = vi.fn()

  beforeEach(() => {
    routerMock.replace.mockClear()
    assign.mockReset()
    Object.defineProperty(window, "location", {
      value: { ...window.location, assign },
      configurable: true,
      writable: true,
    })
  })

  /**
   * Register a `/oauth/providers` override and return a handle that resolves
   * once the page has actually called it.
   *
   * The handle is the point. Every "hides X" assertion below asserts an
   * *absence*, and the SSO section is also absent while the list is still in
   * flight — so `waitFor(() => expect(queryByRole(...)).toBeNull())` succeeds on
   * its very first tick, before the response has even been requested. All five
   * of those tests passed against a page that rendered every provider
   * unconditionally, which is exactly the bug they exist to catch.
   *
   * Awaiting the handler inside `act` also flushes the `.then` that applies the
   * result, so the assertion runs against a settled render.
   */
  async function mockProviders(
    providers: unknown,
    init?: { status?: number },
  ): Promise<{ served: Promise<void> }> {
    const { http, HttpResponse } = await import("msw")
    const { server } = await import("@/test/mocks/server")
    let mark!: () => void
    const served = new Promise<void>((resolve) => {
      mark = resolve
    })
    server.use(
      http.get("/api/auth/oauth/providers", () => {
        mark()
        return HttpResponse.json({ providers }, init)
      }),
    )
    return { served }
  }

  /** Render, then wait until the providers response has been served and applied. */
  async function renderAndSettle(served: Promise<void>) {
    render(<SignInPage />)
    await act(async () => {
      await served
    })
  }

  it("hides a provider the deployment has not configured", async () => {
    const { served } = await mockProviders([
      { name: "google", configured: true },
      { name: "github", configured: false },
    ])

    await renderAndSettle(served)

    // Google is configured, so its button is also the proof the render settled.
    expect(screen.getByRole("button", { name: /continue with google/i })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /continue with github/i })).toBeNull()
  })

  it("hides the whole SSO section when nothing is configured", async () => {
    const { served } = await mockProviders([
      { name: "google", configured: false },
      { name: "github", configured: false },
    ])

    await renderAndSettle(served)

    // "or continue with" is the divider above the buttons; leaving it behind an
    // empty list renders a rule promising options that do not exist.
    expect(screen.queryByText(/or continue with/i)).toBeNull()
    expect(screen.queryByRole("button", { name: /continue with/i })).toBeNull()
    // Password login is untouched: this hides an option, not the page.
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument()
  })

  it("hides the section when the provider list cannot be fetched", async () => {
    // Degradation choice: a sign-in page that cannot reach the server has not
    // learned that SSO exists, and offering a button that will 404 is worse
    // than offering none. Password login works either way.
    const { served } = await mockProviders(
      { detail: "backend_unavailable" },
      { status: 503 },
    )

    await renderAndSettle(served)

    expect(screen.queryByRole("button", { name: /continue with/i })).toBeNull()
    expect(screen.queryByText(/or continue with/i)).toBeNull()
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument()
  })

  it("ignores a provider list served with a failure status", async () => {
    // The failure the `!res.ok` guard is there for, and the one a JSON error
    // body cannot cover.
    //
    // `backendFetch` synthesises `{detail: "backend_timeout"}` on a timeout and
    // `{detail: "backend_unavailable"}` on a dead socket — both harmless, since
    // a body with no `providers` key is filtered out anyway, so the test above
    // proves nothing about the status check. A reverse proxy or CDN in front of
    // the backend is the case that bites: it can answer 502/503 while replaying
    // a cached 200 body, and that body *is* a valid provider list. Parsing it
    // would offer SSO buttons on a response that reported failure.
    const { served } = await mockProviders(
      [{ name: "google", configured: true }],
      { status: 503 },
    )

    await renderAndSettle(served)

    expect(screen.queryByRole("button", { name: /continue with/i })).toBeNull()
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument()
  })

  it("does not show the SSO section before the list arrives", async () => {
    // The wrong direction to flicker: rendering optimistically and hiding a
    // moment later shows the user a button that then vanishes.
    const { served } = await mockProviders([{ name: "google", configured: true }])

    render(<SignInPage />)

    expect(screen.queryByRole("button", { name: /continue with/i })).toBeNull()
    await act(async () => {
      await served
    })
    expect(screen.getByRole("button", { name: /continue with google/i })).toBeInTheDocument()
  })

  it("drops a provider this UI has no button for", async () => {
    // The backend can gain an IdP before the frontend learns to draw it. The
    // name must not become a blank button, and `in`-style checks are also
    // prototype-pollutable: "constructor" is a key on every plain object.
    const { served } = await mockProviders([
      { name: "google", configured: true },
      { name: "okta", configured: true },
      { name: "constructor", configured: true },
    ])

    await renderAndSettle(served)

    expect(screen.getAllByRole("button", { name: /continue with/i })).toHaveLength(1)
    expect(screen.getByRole("button", { name: /continue with google/i })).toBeInTheDocument()
  })

  it("tolerates a malformed provider list", async () => {
    // A shape change on the server must degrade to "no SSO", not throw during
    // render and take the whole sign-in page down.
    const { served } = await mockProviders("not-an-array")

    await renderAndSettle(served)

    expect(screen.queryByRole("button", { name: /continue with/i })).toBeNull()
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument()
  })
})