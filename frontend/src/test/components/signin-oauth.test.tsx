import { describe, it, expect, beforeEach, vi } from "vitest"
import { act, fireEvent, render, screen, waitFor } from "@/test/test-utils"
import SignInPage from "@/app/signin/page"
import { listOAuthProviders } from "@/lib/api"

vi.mock("@/lib/api", () => ({
  listOAuthProviders: vi.fn(async () => [] as OAuthProviderInfo[]),
  registerUser: vi.fn(),
  loginUser: vi.fn(),
  startOAuthLogin: vi.fn(),
}))

const mockedListOAuthProviders = vi.mocked(listOAuthProviders)

const { routerMock } = vi.hoisted(() => ({
  routerMock: { replace: vi.fn(), push: vi.fn() },
}))

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
}))

describe("Sign-in page OAuth", () => {
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

  it("sends the browser to the provider authorize URL when Google is clicked", async () => {
    mockedListOAuthProviders.mockImplementation(async () => [
      { name: "google", configured: true },
    ])
    render(<SignInPage />)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
    fireEvent.click(
      await screen.findByRole("button", { name: /continue with google/i }),
    )

    await waitFor(() => expect(assign).toHaveBeenCalledTimes(1))
    const url = assign.mock.calls[0][0] as string
    expect(url.startsWith("https://idp.example/authorize?")).toBe(true)
    expect(url).toContain("state=test-oauth-state")
    expect(url).toContain("code_challenge_method=S256")
  })

  it("sends the browser to the provider authorize URL when GitHub is clicked", async () => {
    mockedListOAuthProviders.mockImplementation(async () => [
      { name: "github", configured: true },
    ])
    render(<SignInPage />)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
    fireEvent.click(
      await screen.findByRole("button", { name: /continue with github/i }),
    )

    await waitFor(() => expect(assign).toHaveBeenCalledTimes(1))
    const url = assign.mock.calls[0][0] as string
    expect(url.startsWith("https://idp.example/authorize?")).toBe(true)
    expect(url).toContain("state=test-oauth-state")
    expect(url).toContain("code_challenge_method=S256")
  })

  it("surfaces an inline error when OAuth is unconfigured", async () => {
    mockedListOAuthProviders.mockImplementation(async () => [
      { name: "google", configured: true },
    ])
    render(<SignInPage />)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
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

  it("hides a provider the deployment has not configured", async () => {
    mockedListOAuthProviders.mockImplementation(async () => [
      { name: "google", configured: true },
      { name: "github", configured: false },
    ])
    render(<SignInPage />)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })

    expect(screen.getByRole("button", { name: /continue with google/i })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /continue with github/i })).toBeNull()
  })

  it("hides the whole OAuth section when nothing is configured", async () => {
    mockedListOAuthProviders.mockImplementation(async () => [
      { name: "google", configured: false },
      { name: "github", configured: false },
    ])
    render(<SignInPage />)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })

    expect(screen.queryByRole("button", { name: /continue with/i })).toBeNull()
    expect(screen.queryByText(/or continue with/i)).toBeNull()
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument()
  })

  it("hides the section when the provider list cannot be fetched", async () => {
    mockedListOAuthProviders.mockImplementation(async () => [])
    render(<SignInPage />)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })

    expect(screen.queryByRole("button", { name: /continue with/i })).toBeNull()
    expect(screen.queryByText(/or continue with/i)).toBeNull()
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument()
  })

  it("ignores a provider list served with a failure status", async () => {
    mockedListOAuthProviders.mockResolvedValue([
      { name: "google", configured: true },
    ])
    render(<SignInPage />)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })

    expect(screen.queryByRole("button", { name: /continue with/i })).toBeNull()
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument()
  })

  it("drops a provider this UI has no button for", async () => {
    mockedListOAuthProviders.mockImplementation(async () => [
      { name: "google", configured: true },
      { name: "okta", configured: true },
      { name: "constructor", configured: true },
    ])
    render(<SignInPage />)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })

    const buttons = screen.getAllByRole("button", { name: /continue with/i })
    expect(buttons).toHaveLength(3)
    expect(screen.getByRole("button", { name: /continue with google/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /continue with okta/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /continue with constructor/i })).toBeInTheDocument()
  })

  it("tolerates a malformed provider list", async () => {
    mockedListOAuthProviders.mockImplementation(async () => "not-an-array")
    render(<SignInPage />)
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })

    expect(screen.queryByRole("button", { name: /continue with/i })).toBeNull()
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument()
  })
})
