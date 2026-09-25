import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, fireEvent, waitFor } from "@/test/test-utils"
import { AuthModal } from "@/components/AuthModal"

describe("AuthModal", () => {
  const onClose = vi.fn()
  const onSuccess = vi.fn()

  beforeEach(() => {
    onClose.mockReset()
    onSuccess.mockReset()
    document.cookie.split(";").forEach((c) => {
      document.cookie = `${c.split("=")[0].trim()}=; path=/; max-age=0`
    })
  })

  const renderModal = (isOpen = true) =>
    render(<AuthModal isOpen={isOpen} onClose={onClose} onSuccess={onSuccess} />)

  it("renders nothing when closed", () => {
    renderModal(false)
    expect(screen.queryByText("Welcome back")).not.toBeInTheDocument()
  })

  it("logs in and keeps the session without exposing tokens to page scripts", async () => {
    renderModal()
    fireEvent.change(screen.getByPlaceholderText("name@example.com"), {
      target: { value: "test@nexus.ai" },
    })
    fireEvent.change(screen.getByPlaceholderText("••••••••"), {
      target: { value: "password123" },
    })
    fireEvent.click(screen.getByRole("button", { name: /Sign In/ }))

    await waitFor(() => expect(onSuccess).toHaveBeenCalled())
    expect(onClose).not.toHaveBeenCalled()
    // httpOnly tokens are set by the server route handler, never client-side.
    expect(document.cookie).not.toContain("nexus_access_token")
    expect(document.cookie).not.toContain("nexus_refresh_token")
  })

  it("surfaces a readable error when credentials are invalid", async () => {
    renderModal()
    fireEvent.change(screen.getByPlaceholderText("name@example.com"), {
      target: { value: "test@nexus.ai" },
    })
    fireEvent.change(screen.getByPlaceholderText("••••••••"), {
      target: { value: "wrong" },
    })
    fireEvent.click(screen.getByRole("button", { name: /Sign In/ }))

    expect(await screen.findByText("Incorrect email or password.")).toBeInTheDocument()
    expect(onSuccess).not.toHaveBeenCalled()
  })

  it("switches to registration mode and shows the extra fields", () => {
    renderModal()
    fireEvent.click(screen.getByRole("button", { name: "Sign up" }))
    expect(screen.getByPlaceholderText("Jane Doe")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("janedoe")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Get Started/ })).toBeInTheDocument()
  })

  it("registers then logs in when submitted in register mode", async () => {
    renderModal()
    fireEvent.click(screen.getByRole("button", { name: "Sign up" }))
    fireEvent.change(screen.getByPlaceholderText("Jane Doe"), {
      target: { value: "Test User" },
    })
    fireEvent.change(screen.getByPlaceholderText("janedoe"), {
      target: { value: "tester" },
    })
    fireEvent.change(screen.getByPlaceholderText("name@example.com"), {
      target: { value: "test@nexus.ai" },
    })
    fireEvent.change(screen.getByPlaceholderText("••••••••"), {
      target: { value: "password123" },
    })
    fireEvent.click(screen.getByRole("button", { name: /Get Started/ }))

    await waitFor(() => expect(onSuccess).toHaveBeenCalled())
    // No tokens are ever written to document.cookie by page code.
    expect(document.cookie).not.toContain("nexus_access_token")
  })
})