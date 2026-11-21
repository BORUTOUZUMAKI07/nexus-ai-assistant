import { beforeEach, describe, expect, it, vi } from "vitest"
import { act, fireEvent, render, screen, waitFor } from "@/test/test-utils"
import SignInPage from "@/app/signin/page"
import {
  loginUser,
  verifyTwoFactor,
  type OAuthProviderInfo,
} from "@/lib/api"

const { routerMock } = vi.hoisted(() => ({
  routerMock: { replace: vi.fn(), push: vi.fn() },
}))

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
}))

vi.mock("@/lib/api", () => ({
  listOAuthProviders: vi.fn(async (): Promise<OAuthProviderInfo[]> => []),
  registerUser: vi.fn(),
  loginUser: vi.fn(),
  verifyTwoFactor: vi.fn(),
  startOAuthLogin: vi.fn(),
}))

const mockedLoginUser = vi.mocked(loginUser)
const mockedVerifyTwoFactor = vi.mocked(verifyTwoFactor)

async function renderReadySignIn() {
  render(<SignInPage />)
  // The provider lookup runs in an effect; let it settle before interacting so
  // the async state update cannot land in the middle of a form submission.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

async function submitPassword() {
  fireEvent.change(screen.getByLabelText(/email address/i), {
    target: { value: "totp@example.com" },
  })
  fireEvent.change(screen.getByLabelText(/password/i), {
    target: { value: "password123" },
  })
  fireEvent.click(screen.getByRole("button", { name: /sign in/i }))
}

describe("Sign-in page two-factor challenge", () => {
  beforeEach(() => {
    routerMock.replace.mockClear()
    mockedLoginUser.mockReset()
    mockedVerifyTwoFactor.mockReset()
    Object.defineProperty(window, "location", {
      value: { ...window.location, search: "", assign: vi.fn() },
      configurable: true,
      writable: true,
    })
  })

  it("asks for a TOTP code and completes sign-in after verification", async () => {
    mockedLoginUser.mockResolvedValue({
      ok: false,
      status: "2fa_required",
      expires_in: 300,
    })
    mockedVerifyTwoFactor.mockResolvedValue({ ok: true })

    await renderReadySignIn()
    await submitPassword()

    await waitFor(() =>
      expect(screen.getByLabelText(/authentication code/i)).toBeInTheDocument(),
    )
    // A challenge is not a session. Navigating here was the original dead-end:
    // the browser had no cookies yet and bounced straight back to sign-in.
    expect(routerMock.replace).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText(/authentication code/i), {
      target: { value: "123456" },
    })
    fireEvent.click(screen.getByRole("button", { name: /verify/i }))

    await waitFor(() => expect(mockedVerifyTwoFactor).toHaveBeenCalledWith("123456"))
    await waitFor(() => expect(routerMock.replace).toHaveBeenCalledWith("/app"))
  })

  it("shows a failed code without navigating away", async () => {
    mockedLoginUser.mockResolvedValue({
      ok: false,
      status: "2fa_required",
      expires_in: 300,
    })
    mockedVerifyTwoFactor.mockRejectedValue(new Error("Invalid verification code."))

    await renderReadySignIn()
    await submitPassword()
    const codeInput = await screen.findByLabelText(/authentication code/i)
    fireEvent.change(codeInput, { target: { value: "000000" } })
    fireEvent.click(screen.getByRole("button", { name: /verify/i }))

    await waitFor(() =>
      expect(screen.getByText(/invalid verification code/i)).toBeInTheDocument(),
    )
    expect(routerMock.replace).not.toHaveBeenCalled()
  })
})
