import { describe, it, expect, beforeEach, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@/test/test-utils"
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

  it("sends the browser to the provider authorize URL when the SSO button is clicked", async () => {
    render(<SignInPage />)

    fireEvent.click(screen.getByRole("button", { name: /continue with sso/i }))

    await waitFor(() => expect(assign).toHaveBeenCalledTimes(1))
    const url = assign.mock.calls[0][0] as string
    expect(url.startsWith("https://idp.example/authorize?")).toBe(true)
    // The PKCE params come straight from the backend proxy handler.
    expect(url).toContain("state=test-sso-state")
    expect(url).toContain("code_challenge_method=S256")
  })

  it("surfaces an inline error when SSO is unconfigured", async () => {
    // Override the MSW handler for this test: 404 = backend has no OIDC config.
    const { http, HttpResponse } = await import("msw")
    const { server } = await import("@/test/mocks/server")
    server.use(
      http.get("/api/auth/oauth", () =>
        HttpResponse.json(
          { detail: "OAuth single sign-on is not configured." },
          { status: 404 },
        ),
      ),
    )

    render(<SignInPage />)
    fireEvent.click(screen.getByRole("button", { name: /continue with sso/i }))

    await waitFor(() =>
      expect(
        screen.getByText(/single sign-on is not configured/i),
      ).toBeInTheDocument(),
    )
    expect(assign).not.toHaveBeenCalled()
  })
})