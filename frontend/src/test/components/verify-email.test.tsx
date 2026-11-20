import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@/test/test-utils"
import VerifyEmailPage from "@/app/verify-email/page"
import { verifyEmailToken } from "@/lib/api"

vi.mock("@/lib/api", () => ({
  verifyEmailToken: vi.fn(),
}))

const verifyMock = vi.mocked(verifyEmailToken)

function setSearch(search: string) {
  window.history.replaceState({}, "", `/${search}`)
}

describe("email verification page", () => {
  beforeEach(() => {
    verifyMock.mockReset()
    setSearch("?token=verify-token-123")
  })

  it("redeems the token and offers a way into the app", async () => {
    verifyMock.mockResolvedValue({ id: "u1", email: "user@example.com", is_verified: true })
    render(<VerifyEmailPage />)

    await waitFor(() => expect(verifyMock).toHaveBeenCalledWith("verify-token-123"))
    expect(await screen.findByText(/email verified/i)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /sign in/i })).toHaveAttribute("href", "/signin")
  })

  it("shows the backend message when the token is invalid", async () => {
    verifyMock.mockRejectedValue(new Error("Invalid or expired verification token."))
    render(<VerifyEmailPage />)

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /invalid or expired verification token/i
    )
    expect(verifyMock).toHaveBeenCalledTimes(1)
  })

  it("does not call the API when the link has no token", () => {
    setSearch("?unexpected=1")
    render(<VerifyEmailPage />)

    expect(screen.getByRole("alert")).toHaveTextContent(/missing its token/i)
    expect(verifyMock).not.toHaveBeenCalled()
  })
})
