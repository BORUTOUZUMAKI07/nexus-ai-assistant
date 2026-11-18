import { describe, it, expect, beforeEach, vi } from "vitest"
import { fireEvent, render, screen } from "@/test/test-utils"
import SignInPage from "@/app/signin/page"
import InvitePage from "@/app/invite/page"
import { AdminView } from "@/features/admin/AdminView"

const { routerMock } = vi.hoisted(() => ({
  routerMock: { replace: vi.fn(), push: vi.fn() },
}))

vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))

/** Put a query string in place before the page's useSyncExternalStore snapshot. */
function setSearch(search: string) {
  window.history.replaceState({}, "", `/${search}`)
}

describe("sign-in form accessibility", () => {
  beforeEach(() => {
    routerMock.replace.mockClear()
    setSearch("?error=oauth_no_token")
  })

  it("announces the error region assertively", () => {
    render(<SignInPage />)
    const alert = screen.getByRole("alert")
    expect(alert).toHaveTextContent(/could not complete sign-in/i)
    expect(alert).toHaveAttribute("aria-live", "assertive")
  })

  it("associates every visible label with its input", () => {
    render(<SignInPage />)
    for (const name of [/email address/i, /password/i]) {
      const input = screen.getByLabelText(name)
      expect(input.tagName).toBe("INPUT")
      expect(input).toHaveAttribute("id")
    }
  })

  it("associates registration-only labels when the register form is shown", () => {
    render(<SignInPage />)
    fireEvent.click(screen.getByRole("button", { name: /sign up/i }))
    for (const name of [/full name/i, /username/i]) {
      const input = screen.getByLabelText(name)
      expect(input.tagName).toBe("INPUT")
      expect(input).toHaveAttribute("id")
    }
  })
})

describe("invite page accessibility", () => {
  beforeEach(() => setSearch("?unexpected=1"))

  it("announces failure states as an alert", () => {
    render(<InvitePage />)
    expect(screen.getByRole("alert")).toHaveTextContent(/missing its token/i)
  })
})

describe("admin form labels", () => {
  it("gives every placeholder-only control a persistent label", async () => {
    render(<AdminView />)
    fireEvent.click(screen.getByText("Lifecycle hooks"))
    await screen.findByText("Block shell exec")

    for (const label of [
      "Policy name",
      "Tool name",
      "Event",
      "Action",
      "Field to redact",
      "Organization ID",
      "Block message",
    ]) {
      expect(screen.getByLabelText(label), label).toBeInTheDocument()
    }

    fireEvent.click(screen.getByText("Optimization"))
    await screen.findByText("Optimization evidence trail")
    for (const label of [
      "Prompt key",
      "Candidates",
      "Baseline prompt",
      "Golden cases",
    ]) {
      expect(screen.getByLabelText(label), label).toBeInTheDocument()
    }
  })
})
