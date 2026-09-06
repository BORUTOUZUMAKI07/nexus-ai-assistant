import { describe, it, expect } from "vitest"
import { render, screen, fireEvent, waitFor } from "@/test/test-utils"
import Home from "@/app/page"

function clearCookie() {
  document.cookie.split(";").forEach((c) => {
    document.cookie = `${c.split("=")[0].trim()}=; path=/; max-age=0`
  })
}

describe("Home page integration", () => {
  it("opens the login gate when no token is present", async () => {
    clearCookie()
    render(<Home />)
    expect(await screen.findByText("Welcome back")).toBeInTheDocument()
  })

  it("logs in, loads conversations, and streams an assistant reply", async () => {
    clearCookie()
    render(<Home />)

    // Login through the gate
    await screen.findByText("Welcome back")
    fireEvent.change(screen.getByPlaceholderText("name@example.com"), {
      target: { value: "test@nexus.ai" },
    })
    fireEvent.change(screen.getByPlaceholderText("••••••••"), {
      target: { value: "password123" },
    })
    fireEvent.click(screen.getByRole("button", { name: /Sign In/ }))

    // Conversations load into the sidebar after the modal closes
    await waitFor(() =>
      expect(screen.queryByText("Welcome back")).not.toBeInTheDocument()
    )
    await screen.findByText("Project kickoff")

    // Send a message; the assistant streams back through /api/chat
    const textarea = screen.getByPlaceholderText(
      "Ask Nexus anything, write code, search live web..."
    )
    fireEvent.change(textarea, { target: { value: "Hello!" } })
    fireEvent.click(screen.getByTitle("Send message (Enter)"))

    expect(await screen.findByText("Hello from Nexus.")).toBeInTheDocument()
  })

  it("supports sign out back to the login gate", async () => {
    clearCookie()
    render(<Home />)
    await screen.findByText("Welcome back")
    fireEvent.change(screen.getByPlaceholderText("name@example.com"), {
      target: { value: "test@nexus.ai" },
    })
    fireEvent.change(screen.getByPlaceholderText("••••••••"), {
      target: { value: "password123" },
    })
    fireEvent.click(screen.getByRole("button", { name: /Sign In/ }))

    await screen.findByText("Project kickoff")
    fireEvent.click(screen.getByTitle("Sign out"))

    expect(await screen.findByText("Welcome back")).toBeInTheDocument()
  })
})