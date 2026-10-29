import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, fireEvent, waitFor } from "@/test/test-utils"
import { http, HttpResponse } from "msw"
import { server } from "@/test/mocks/server"
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

/** Log in through the gate and wait for the sidebar to populate. */
async function login() {
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

  it("does not rename a just-branched conversation to its first message", async () => {
    // The live walk found this: `branchFromMessage` returned only the fork's
    // id and the page set it active, while the fork was still *absent* from
    // the conversation list. The auto-titler read absence as "untitled" and
    // renamed the fork to its first user message, destroying the backend's
    // `"<parent> (Branch: …)"` title. The list here never learns about the
    // fork — which is exactly the race — so the only thing that can protect
    // the title is the titler's own guard.
    const renames: Array<{ id: string; title: string }> = []
    let forkCount = 0
    server.use(
      http.post("/api/conversations/:id/fork", async () => {
        forkCount += 1
        return HttpResponse.json({
          id: "conv-forked-branch",
          title: "Project kickoff (Branch: Forked Branch)",
          model: "llama-3.3-70b-versatile",
          is_pinned: false,
          is_archived: false,
          project_id: null,
          token_count: 0,
          created_at: "2026-09-06T00:00:00Z",
          updated_at: "2026-09-06T00:00:00Z",
        })
      }),
      http.patch("/api/conversations/:id", async ({ request, params }) => {
        const patch = (await request.json()) as { title?: string }
        renames.push({ id: String(params.id), title: patch.title ?? "" })
        return HttpResponse.json({ id: params.id, ...patch })
      })
    )

    await login()
    fireEvent.click(screen.getByText("Project kickoff"))
    await screen.findByText("Tell me about RAG")

    fireEvent.click(screen.getAllByTitle("Branch conversation from here")[0])
    await waitFor(() => expect(forkCount).toBe(1))

    // The auto-titler runs once the fork is active and the transcript has
    // hydrated; give it a real window to fire. A `waitFor` on a *negative*
    // condition cannot tell "has not fired yet" from "will never fire", so
    // flush effects on a timer and then assert nothing was renamed.
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(renames).toEqual([])
  })

  it("shows the branch in the sidebar before the list refetch lands", async () => {
    // The second half of the branch fix: the fork is inserted into the
    // conversation list from the object `branchFromMessage` returned. The list
    // refetch is held open here, so a sidebar that names the fork *while the
    // refetch is pending* can only have got it from that insert. Without the
    // insert the sidebar shows no sign the branch happened until the refetch
    // catches up.
    const forkedRow = {
      id: "conv-forked-branch",
      title: "Project kickoff (Branch: Forked Branch)",
      model: "llama-3.3-70b-versatile",
      is_pinned: false,
      is_archived: false,
      project_id: null,
      token_count: 0,
      created_at: "2026-09-06T00:00:00Z",
      updated_at: "2026-09-06T00:00:00Z",
    }
    // A mutable holder, not a `let`: TypeScript narrows a `let` assigned only
    // inside a closure to `null` at the call site (the closure may not have
    // run), which makes the release call `never`-callable. Reading through an
    // object keeps the type honest.
    const gate: { release: (() => void) | null } = { release: null }
    let forkCount = 0
    const base = {
      id: "conv-100",
      title: "Project kickoff",
      model: "llama-3.3-70b-versatile",
      is_pinned: true,
      is_archived: false,
      token_count: 12850,
      created_at: "2026-09-01T10:00:00Z",
      updated_at: "2026-09-02T10:00:00Z",
    }
    server.use(
      http.post("/api/conversations/:id/fork", async () => {
        forkCount += 1
        return HttpResponse.json(forkedRow)
      }),
      http.get("/api/conversations", async () => {
        // After the fork, hold the response until the test releases it. The
        // first call (login) resolves immediately so the sidebar can populate.
        if (forkCount === 0) return HttpResponse.json([base])
        await new Promise<void>((resolve) => {
          gate.release = resolve
        })
        return HttpResponse.json([forkedRow, base])
      })
    )

    await login()
    fireEvent.click(screen.getByText("Project kickoff"))
    await screen.findByText("Tell me about RAG")

    fireEvent.click(screen.getAllByTitle("Branch conversation from here")[0])
    await waitFor(() => expect(forkCount).toBe(1))

    // The refetch is parked, so this title can only come from the seeded cache.
    expect(
      await screen.findByText("Project kickoff (Branch: Forked Branch)")
    ).toBeInTheDocument()

    gate.release?.()
  })
})