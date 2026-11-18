import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import { render, screen, fireEvent, waitFor, act } from "@/test/test-utils"
import { http, HttpResponse } from "msw"
import { server } from "@/test/mocks/server"
import Home from "@/app/app/page"
import { mockConversations, mockSettings } from "@/test/mocks/handlers"

const { routerMock, authState, toastMock } = vi.hoisted(() => ({
  routerMock: { replace: vi.fn(), push: vi.fn() },
  // MSW cannot see the httpOnly cookies the real /api/auth/login sets, so the
  // bootstrap mock has to be told when the test's login succeeded. Without the
  // flip, a successful login would immediately refetch a stale
  // `{ authenticated: false }` verdict and reopen the modal.
  authState: { signedIn: false },
  // The real Toaster lives in the root layout, which this test does not render.
  // Spy on the calls instead so a silent console.warn cannot pass as feedback.
  toastMock: {
    loading: vi.fn(() => "feedback-toast"),
    success: vi.fn(),
    error: vi.fn(),
    dismiss: vi.fn(),
  },
}))

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
}))

vi.mock("sonner", () => ({ toast: toastMock }))

function clearCookie() {
  document.cookie.split(";").forEach((c) => {
    document.cookie = `${c.split("=")[0].trim()}=; path=/; max-age=0`
  })
}

/**
 * Model the two halves of a session with the aggregate endpoint: signed out on
 * first paint, signed in for every bootstrap refetch after a successful login.
 * The login handler keeps the same credential check as the shared mock, so the
 * existing "wrong password" behaviour is not lost.
 */
function mockAuthLifecycle() {
  authState.signedIn = false
  server.use(
    http.post("/api/auth/login", async ({ request }) => {
      const contentType = request.headers.get("content-type") ?? ""
      let email = ""
      let password = ""
      if (contentType.includes("application/x-www-form-urlencoded")) {
        const params = new URLSearchParams(await request.text())
        email = params.get("username") ?? ""
        password = params.get("password") ?? ""
      } else {
        const body = (await request.json()) as { email?: string; password?: string }
        email = body.email ?? ""
        password = body.password ?? ""
      }
      if (!email || password !== "password123") {
        return HttpResponse.json(
          { detail: "Incorrect email or password." },
          { status: 401 }
        )
      }
      authState.signedIn = true
      return HttpResponse.json({ ok: true })
    }),
    http.get("/api/bootstrap", () =>
      authState.signedIn
        ? HttpResponse.json({
            authenticated: true,
            user: { id: "user-1", email: "test@nexus.ai", role: "user" },
            conversations: {
              items: mockConversations,
              total: mockConversations.length,
              page: 1,
              size: 50,
              next_cursor: null,
              has_more: false,
            },
            settings: mockSettings,
          })
        : HttpResponse.json({ authenticated: false })
    )
  )
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
  await screen.findAllByText("Project kickoff")
}

describe("Home page integration", () => {
  beforeEach(() => {
    routerMock.replace.mockClear()
    routerMock.push.mockClear()
    toastMock.loading.mockClear()
    toastMock.success.mockClear()
    toastMock.error.mockClear()
    toastMock.dismiss.mockClear()
    mockAuthLifecycle()
  })

  afterEach(() => {
    vi.useRealTimers()
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
    await screen.findAllByText("Project kickoff")

    // Send a message; the assistant streams back through /api/chat
    const textarea = screen.getByPlaceholderText(
      "Ask Nexus anything, write code, search live web..."
    )
    fireEvent.change(textarea, { target: { value: "Hello!" } })
    fireEvent.click(screen.getByTitle("Send message (Enter)"))

    expect(await screen.findByText("Hello from Nexus.")).toBeInTheDocument()
  })

  it("signs out with a full navigation to the sign-in page", async () => {
    // Sign-out used to end with `router.replace("/")` — a client-side hop to the
    // marketing page that keeps this tree mounted for a tick. The cache clear
    // makes every query refetch with the cookies already gone, those 401s fire
    // SESSION_EXPIRED_EVENT, and the in-app sign-in *dialog* opened over the
    // workspace: the "small sign-in page" that appeared after signing out. A real
    // page load to /signin is the url-shortner's behaviour and the fix.
    clearCookie()
    const assign = vi.fn()
    Object.defineProperty(window, "location", {
      value: { ...window.location, assign },
      configurable: true,
      writable: true,
    })

    render(<Home />)
    await screen.findByText("Welcome back")
    fireEvent.change(screen.getByPlaceholderText("name@example.com"), {
      target: { value: "test@nexus.ai" },
    })
    fireEvent.change(screen.getByPlaceholderText("••••••••"), {
      target: { value: "password123" },
    })
    fireEvent.click(screen.getByRole("button", { name: /Sign In/ }))

    await screen.findAllByText("Project kickoff")
    fireEvent.click(screen.getByTitle("Sign out"))

    // The logout route answered 200, so the cookies are gone and the plain page
    // is reachable — no `expired` escape hatch needed.
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/signin"))
    // And nothing was routed client-side: a router hop is exactly what kept the
    // tree (and its event listeners) alive long enough to open the dialog.
    expect(routerMock.replace).not.toHaveBeenCalledWith("/")
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
    await screen.findAllByText("Project kickoff")

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
    await screen.findAllByText("Project kickoff")

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
    await screen.findAllByText("Project kickoff")

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
    fireEvent.click(screen.getAllByText("Project kickoff")[0])
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
    fireEvent.click(screen.getAllByText("Project kickoff")[0])
    await screen.findByText("Tell me about RAG")

    fireEvent.click(screen.getAllByTitle("Branch conversation from here")[0])
    await waitFor(() => expect(forkCount).toBe(1))

    // The refetch is parked, so this title can only come from the seeded cache.
    expect(
      await screen.findByText("Project kickoff (Branch: Forked Branch)")
    ).toBeInTheDocument()

    gate.release?.()
  })

  it("renders the persisted cost and token count of a history-loaded reply", async () => {
    // The page maps NexusMessage -> MessageItem by hand. Both fields already
    // survive the history mapper (`messagesFromHistory`), so dropping them
    // here made ChatArea's cost pill and token chip unreachable for any reply
    // that was not produced live in this tab.
    server.use(
      http.get("/api/conversations/:id", () =>
        HttpResponse.json({
          ...mockConversations[0],
          system_prompt: null,
          messages: [
            {
              id: "msg-cost-user",
              role: "user",
              content: "What did that cost?",
              thought_process: null,
              model: null,
              prompt_tokens: 10,
              completion_tokens: 0,
              total_tokens: 10,
              cost_usd: 0,
              citations: [],
              tool_calls: [],
              user_feedback: null,
              created_at: "2026-09-02T10:00:00Z",
            },
            {
              id: "msg-cost-assistant",
              role: "assistant",
              content: "That turn used 456 tokens.",
              thought_process: null,
              model: "llama-3.3-70b-versatile",
              prompt_tokens: 300,
              completion_tokens: 156,
              total_tokens: 456,
              cost_usd: 0.0123,
              citations: [],
              tool_calls: [],
              user_feedback: null,
              created_at: "2026-09-02T10:00:01Z",
            },
          ],
        })
      )
    )

    await login()
    fireEvent.click(screen.getAllByText("Project kickoff")[0])
    expect(
      await screen.findByText("That turn used 456 tokens.")
    ).toBeInTheDocument()
    expect(screen.getByText("$0.0123")).toBeInTheDocument()
    expect(screen.getByText("456 tokens")).toBeInTheDocument()
  })

  it("opens the mobile navigation drawer as a focus-managed modal dialog", async () => {
    await login()
    const menu = screen.getByRole("button", { name: "Open navigation" })
    expect(menu).toHaveAttribute("aria-expanded", "false")
    expect(menu).toHaveAttribute("aria-controls", "mobile-nav-drawer")

    fireEvent.click(menu)
    const dialog = await screen.findByRole("dialog", { name: /navigation/i })
    expect(dialog).toHaveAttribute("aria-modal", "true")
    // Initial focus must land inside the drawer, not stay on the trigger.
    expect(dialog).toContainElement(document.activeElement as HTMLElement)

    fireEvent.keyDown(document.activeElement as HTMLElement, { key: "Escape" })
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: /navigation/i })
      ).not.toBeInTheDocument()
    )
    expect(menu).toHaveAttribute("aria-expanded", "false")
    expect(document.activeElement).toBe(menu)
  })

  it("keeps the compare control reachable from the mobile bar after bootstrap hydration", async () => {
    await login()
    // Two buttons share the name: the desktop header is display-hidden under
    // md, so the mobile bar's copy is the one a small-screen user can reach.
    const compareButtons = screen.getAllByRole("button", {
      name: "Compare models side-by-side",
    })
    fireEvent.click(compareButtons[compareButtons.length - 1])
    expect(
      await screen.findByRole("dialog", { name: /Model Comparison/i })
    ).toBeInTheDocument()
  })

  it("shows a failed feedback submission and rolls back the optimistic state", async () => {
    let attempts = 0
    server.use(
      http.post(
        "/api/conversations/:id/messages/:messageId/feedback",
        () => {
          attempts += 1
          return HttpResponse.json({ detail: "feedback unavailable" }, { status: 500 })
        }
      )
    )

    await login()
    fireEvent.click(screen.getAllByText("Project kickoff")[0])
    expect(
      await screen.findByText("RAG grounds answers in your indexed documents.")
    ).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Good response" }))

    // The optimistic state is visible immediately, but it is not durable.
    expect(toastMock.loading).toHaveBeenCalled()
    expect(screen.getByText("Saving feedback: Good response…")).toBeInTheDocument()

    await waitFor(() => expect(toastMock.error).toHaveBeenCalled())
    expect(toastMock.success).not.toHaveBeenCalled()
    expect(screen.queryByText("Saving feedback: Good response…")).not.toBeInTheDocument()
    expect(screen.queryByText("Feedback saved: Good response")).not.toBeInTheDocument()
    expect(attempts).toBe(1)
  })

  it("dismisses a saved-feedback notice instead of leaving it on screen forever", async () => {
    server.use(
      http.post(
        "/api/conversations/:id/messages/:messageId/feedback",
        () => HttpResponse.json({ ok: true })
      )
    )

    await login()
    fireEvent.click(screen.getAllByText("Project kickoff")[0])
    expect(
      await screen.findByText("RAG grounds answers in your indexed documents.")
    ).toBeInTheDocument()

    // Fake timers start only after the shell is hydrated, so React Query's
    // bootstrap backoff stays on real timers. The notice's dismissal timer is
    // armed by the click below.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    fireEvent.click(screen.getByRole("button", { name: "Good response" }))

    expect(await screen.findByText("Feedback saved: Good response")).toBeInTheDocument()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    expect(
      screen.queryByText("Feedback saved: Good response")
    ).not.toBeInTheDocument()
  })
})