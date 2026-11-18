/**
 * Shell hydration through the aggregate bootstrap endpoint.
 *
 * Before this slice the `/app` shell made three browser round trips on mount
 * (`/api/auth/me`, then the conversations and settings queries) and the three
 * payloads landed in three different places at three different times. These
 * tests pin the new contract: ONE bootstrap request, zero legacy mount calls,
 * every domain cache seeded from that one response, and a cold-start
 * `unavailable` verdict retried rather than read as "signed out".
 */
import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor, fireEvent, act } from "@/test/test-utils"
import { http, HttpResponse } from "msw"
import { server } from "@/test/mocks/server"
import Home from "@/app/app/page"
import { SESSION_EXPIRED_EVENT } from "@/lib/auth"
import { mockConversations, mockSettings } from "@/test/mocks/handlers"

const { routerMock } = vi.hoisted(() => ({
  routerMock: { replace: vi.fn(), push: vi.fn() },
}))

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
}))

const BOOTSTRAP_CONVERSATION = {
  ...mockConversations[0],
  id: "conv-bootstrap",
  title: "Bootstrapped conversation",
}

function authenticatedBootstrap() {
  return {
    authenticated: true as const,
    user: { id: "user-1", email: "test@nexus.ai", role: "admin" },
    conversations: {
      items: [BOOTSTRAP_CONVERSATION],
      total: 1,
      page: 1,
      size: 50,
      next_cursor: null,
      has_more: false,
    },
    settings: mockSettings,
  }
}

const UNAVAILABLE = {
  authenticated: false as const,
  unavailable: true as const,
  detail: "auth_unavailable",
  upstream_status: 503,
}

describe("app shell bootstrap hydration", () => {
  beforeEach(() => {
    routerMock.replace.mockClear()
    routerMock.push.mockClear()
  })

  it("makes exactly one bootstrap call and no legacy mount fetches", async () => {
    let bootstrapCalls = 0
    let legacyCalls = 0
    server.use(
      http.get("/api/bootstrap", () => {
        bootstrapCalls += 1
        return HttpResponse.json(authenticatedBootstrap())
      }),
      http.get("/api/auth/me", () => {
        legacyCalls += 1
        return HttpResponse.json({ authenticated: false })
      }),
      http.get("/api/conversations", () => {
        legacyCalls += 1
        return HttpResponse.json([BOOTSTRAP_CONVERSATION])
      }),
      http.get("/api/settings", () => {
        legacyCalls += 1
        return HttpResponse.json(mockSettings)
      }),
    )

    render(<Home />)

    // The title exists ONLY in the bootstrap payload, so rendering it proves
    // the conversation cache was seeded from that response rather than from a
    // separate /api/conversations fetch.
    expect(
      (await screen.findAllByText("Bootstrapped conversation")).length
    ).toBeGreaterThan(0)
    expect(bootstrapCalls).toBe(1)
    expect(legacyCalls).toBe(0)
    // `isAdmin` comes from bootstrap.user, not a later /api/auth/me probe.
    expect(screen.getByText("Admin")).toBeInTheDocument()
  })

  it("retries an unavailable bootstrap verdict without showing the sign-in gate", async () => {
    let calls = 0
    server.use(
      http.get("/api/bootstrap", () => {
        calls += 1
        if (calls === 1) return HttpResponse.json(UNAVAILABLE)
        return HttpResponse.json(authenticatedBootstrap())
      }),
    )

    render(<Home />)

    await waitFor(() => expect(calls).toBe(2), { timeout: 3000 })
    expect(
      (await screen.findAllByText("Bootstrapped conversation")).length
    ).toBeGreaterThan(0)
    expect(screen.queryByText("Welcome back")).not.toBeInTheDocument()
  })

  it("drops the cached true verdict on session expiry and re-proves auth on login", async () => {
    const state = { signedIn: true }
    let calls = 0
    server.use(
      http.get("/api/bootstrap", () => {
        calls += 1
        return HttpResponse.json(
          state.signedIn
            ? authenticatedBootstrap()
            : { authenticated: false },
        )
      }),
      http.post("/api/auth/login", () => {
        state.signedIn = true
        return HttpResponse.json({ ok: true })
      }),
    )

    render(<Home />)
    expect(
      (await screen.findAllByText("Bootstrapped conversation")).length
    ).toBeGreaterThan(0)
    expect(calls).toBe(1)

    state.signedIn = false
    act(() => {
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT))
    })
    expect(await screen.findByText("Welcome back")).toBeInTheDocument()

    // No cached `true` may survive the reset: signing back in must fetch a
    // fresh verdict rather than re-use the one from before the expiry.
    fireEvent.change(screen.getByPlaceholderText("name@example.com"), {
      target: { value: "test@nexus.ai" },
    })
    fireEvent.change(screen.getByPlaceholderText("••••••••"), {
      target: { value: "password123" },
    })
    fireEvent.click(screen.getByRole("button", { name: /Sign In/ }))
    await waitFor(() => expect(calls).toBe(2))
    expect(
      (await screen.findAllByText("Bootstrapped conversation")).length
    ).toBeGreaterThan(0)
  })
})
