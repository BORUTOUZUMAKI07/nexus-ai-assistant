import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, fireEvent, waitFor } from "@/test/test-utils"
import Home from "@/app/app/page"

const { routerMock } = vi.hoisted(() => ({
  routerMock: { replace: vi.fn(), push: vi.fn() },
}))

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
}))

function clearCookie() {
  document.cookie.split(";").forEach((c) => {
    document.cookie = `${c.split("=")[0].trim()}=; path=/; max-age=0`
  })
}

describe("Home page integration", () => {
  beforeEach(() => {
    routerMock.replace.mockClear()
    routerMock.push.mockClear()
  })

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

  it("supports sign out by clearing the token and redirecting to the landing page", async () => {
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

    await waitFor(() => expect(routerMock.replace).toHaveBeenCalledWith("/"))
  })

  it("plans in Plan mode: drafts a plan card, then executes it on approval", async () => {
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
    await screen.findByText("Project kickoff")

    // Enable the Plan toggle and send a task
    fireEvent.click(screen.getByTitle("Plan mode OFF: respond directly"))
    const textarea = screen.getByPlaceholderText(
      "Ask Nexus anything, write code, search live web..."
    )
    fireEvent.change(textarea, { target: { value: "Refactor the auth layer" } })
    fireEvent.click(screen.getByTitle("Send message (Enter)"))

    // A plan review card appears instead of a direct answer
    expect(await screen.findByText("Proposed Plan")).toBeInTheDocument()
    expect(screen.getByText("Refactor the auth layer")).toBeInTheDocument()

    // Approving commits the plan and streams the agent execution
    fireEvent.click(screen.getByText("Approve & Execute"))
    expect(await screen.findByText("Hello from Nexus.")).toBeInTheDocument()
    expect(screen.queryByText("Proposed Plan")).not.toBeInTheDocument()
  })

  it("surfaces artifacts saved in an earlier session, not just this one's code blocks", async () => {
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

    // Loaded from the server, so the artifact survives a reload. The canvas is
    // not open yet — nothing in the message stream opened it.
    expect(await screen.findByText("Saved artifacts")).toBeInTheDocument()
    expect(screen.getByText("auth.py")).toBeInTheDocument()

    fireEvent.click(screen.getByText("auth.py"))
    expect(await screen.findByText("def auth():")).toBeInTheDocument()

    // The list endpoint returns current versions but has no isActiveVersion
    // field, so the page has to mark it. Without that the canvas would label a
    // saved artifact as a mere snapshot of the version it actually is.
    expect(screen.getByText("v2 • saved")).toBeInTheDocument()
  })

  it("surfaces the conversation's plan history even with none drafted this session", async () => {
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

    // The stored plan is listed even though no plan was drafted in this session.
    expect(await screen.findByText("Refactor auth service")).toBeInTheDocument()
    expect(screen.getByText("Awaiting decision")).toBeInTheDocument()
  })
})