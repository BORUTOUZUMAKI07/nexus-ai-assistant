import { expect, test as base, type Page, type Route } from "@playwright/test"

export const AUTH_COOKIE = "nexus_access_token"
export const TEST_TOKEN = "test-access-token"
export const ADMIN_TOKEN = "test-admin-token"

export const TEST_CONVERSATION = {
  id: "conv-100",
  title: "Project kickoff",
  mode: "normal",
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-02T10:00:00Z",
  message_count: 5,
}

// Vercel AI SDK data-stream protocol body served by /api/chat
export const CHAT_STREAM = `0:"Hello from Nexus."\n`

export function buildChatStream(body: string): { status: number; contentType: string; body: string } {
  return { status: 200, contentType: "text/plain; charset=utf-8", body }
}

type Handler = { pattern: RegExp | string; handler: (route: Route) => void }

// One shared registry of handlers per page so independent mock groups compose
// instead of each registering competing catch-all routes.
const registries = new WeakMap<
  Page,
  { handlers: Handler[]; capturedChatRequests: Record<string, unknown>[] }
>()

function registryFor(page: Page) {
  let entry = registries.get(page)
  if (!entry) {
    entry = { handlers: [], capturedChatRequests: [] }
    registries.set(page, entry)
  }
  return entry
}

export function mockApi(page: Page) {
  const entry = registryFor(page)

  function route(pattern: RegExp | string, handler: (route: Route) => void) {
    entry.handlers.push({ pattern, handler })
  }

  async function setup() {
    await page.route("**/api/**", (route) => {
      const url = route.request().url()
      const cookieStr = route.request().headers()["cookie"] ?? ""
      const hasToken = cookieStr
        .split(";")
        .some((c) => c.trim().startsWith(`${AUTH_COOKIE}=`))

      // The app gates on /api/auth/me since the httpOnly access cookie is not
      // readable from page scripts. Authenticated exactly when the access token
      // cookie is present; the Admin nav is derived from the role in the probe.
      if (url.includes("/api/auth/me")) {
        const tokenMatch = cookieStr
          .split(";")
          .map((c) => c.trim())
          .find((c) => c.startsWith(`${AUTH_COOKIE}=`))
        const token = tokenMatch ? decodeURIComponent(tokenMatch.slice(AUTH_COOKIE.length + 1)) : ""
        const admin = token === ADMIN_TOKEN
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(
            hasToken
              ? {
                  authenticated: true,
                  user: { id: "user-1", email: "test@nexus.ai", role: admin ? "admin" : "user" },
                }
              : { authenticated: false }
          ),
        })
      }
      // Sign-out clears the access cookie the same way the real route does.
      if (url.includes("/api/auth/logout")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          headers: {
            "Set-Cookie": `${AUTH_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`,
          },
          body: JSON.stringify({ message: "Logged out successfully" }),
        })
      }
      if (url.includes("/api/chat")) {
        const body = route.request().postDataJSON?.() ?? {}
        entry.capturedChatRequests.push(body)
      }
      for (const h of entry.handlers) {
        if (typeof h.pattern === "string" ? url.includes(h.pattern) : h.pattern.test(url)) {
          return h.handler(route)
        }
      }
      if (url.includes("/api/chat")) {
        return route.fulfill(buildChatStream(CHAT_STREAM))
      }
      // Loading the active conversation's history is part of the app shell, not
      // of what an individual spec asserts — every hydrate fetches it. Falling
      // through to the 404 below made specs that never cared about history print
      // "Failed to load conversation history: 404" in the console. That noise is
      // worse than silence: it trains a reader to ignore the console, which is
      // precisely where a genuine 404 would then be missed.
      // Anchored to the exact detail path: a prefix match also swallows nested
      // resources (`/plans`, `/messages`), and answering `{}` where the app expects
      // an array breaks rendering in a way that looks like a broken sidebar.
      if (route.request().method() === "GET" && /\/api\/conversations\/[^/?#]+$/.test(new URL(url).pathname)) {
        return route.fulfill({ status: 200, contentType: "application/json", body: "{}" })
      }
      route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "unmocked" }),
      })
    })
  }

  return { route, setup, capturedChatRequests: entry.capturedChatRequests }
}

export function setupAuthMocks(page: Page) {
  const mocks = mockApi(page)

  const authCookieHeaders = (accessToken: string) => ({
    // Playwright's Route.fulfill only accepts a flat headers dict, so a single
    // Set-Cookie is set. The access cookie is the one the /api/auth/me gate
    // tests for; the refresh cookie isn't consumed by any e2e assertion.
    "Set-Cookie": `${AUTH_COOKIE}=${accessToken}; Path=/; Max-Age=604800; SameSite=Lax`,
  })

  mocks.route(/\/api\/auth\/login$/, (route) => {
    const body = route.request().postDataJSON?.() ?? {}
    if (body.password !== "password123") {
      return route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Incorrect email or password." }),
      })
    }
    route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: authCookieHeaders(TEST_TOKEN),
      // Mirrors the real route handler: the browser only learns success/failure.
      body: JSON.stringify({ ok: true }),
    })
  })

  mocks.route(/\/api\/auth\/refresh$/, (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: authCookieHeaders("test-refreshed-access-token"),
      body: JSON.stringify({ ok: true }),
    })
  })

  mocks.route(/\/api\/auth\/register$/, (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: "user-1", email: "test@nexus.ai" }),
    })
  })

  return mocks
}

export function setupConversationMocks(
  page: Page,
  conversations: unknown[] = [TEST_CONVERSATION]
) {
  const mocks = mockApi(page)

  mocks.route(/\/api\/conversations\/[^/?#]+(?:\?[^#]*)?$/, (route) => {
    if (route.request().method() === "DELETE") {
      return route.fulfill({ status: 204 })
    }
    route.fulfill({ status: 200, contentType: "application/json", body: "{}" })
  })

  mocks.route(/\/api\/conversations(?:\?[^#]*)?$/, (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(TEST_CONVERSATION),
      })
    }
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(conversations),
    })
  })

  return mocks
}

export function setupFilesMock(page: Page) {
  const mocks = mockApi(page)

  mocks.route(/\/api\/files(\?|$)/, (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "file-1",
          filename: "nexus-spec.pdf",
          status: "indexed",
          size_bytes: 145200,
          chunk_count: 42,
          created_at: "2026-09-01T10:00:00Z",
        },
      ]),
    })
  })

  return mocks
}

export function setupSettingsMock(page: Page) {
  const mocks = mockApi(page)

  mocks.route(/\/api\/settings\/memories(\?|$)/, (route) => {
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
  })

  mocks.route(/\/api\/settings\/keys(\?|$)/, (route) => {
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
  })

  mocks.route(/\/api\/settings(\?|$)/, (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        system_prompt_override: null,
        default_model: "llama-3.3-70b-versatile",
        enable_memory: true,
        enable_tools: true,
        temperature: 0.7,
        max_tokens: 4096,
      }),
    })
  })

  return mocks
}

export function setupChatMock(page: Page, streamBody: string = CHAT_STREAM) {
  const mocks = mockApi(page)

  mocks.route(/\/api\/chat$/, (route) => {
    route.fulfill(buildChatStream(streamBody))
  })

  return mocks
}

export async function setAuthState(page: Page, accessToken = TEST_TOKEN) {
  await page.addInitScript(({ token, name }) => {
    document.cookie = `${name}=${encodeURIComponent(token)}; path=/; max-age=604800; samesite=lax`
  }, { token: accessToken, name: AUTH_COOKIE })
}

export class NexusAppPage {
  constructor(public page: Page) {}

  async goto() {
    await this.page.goto("/app")
  }

  async login(email: string, password: string) {
    await this.page.getByPlaceholder("name@example.com").fill(email)
    await this.page.getByPlaceholder("••••••••").fill(password)
    await this.page.getByRole("button", { name: /Sign In/ }).click()
  }

  async sendMessage(text: string) {
    await this.page
      .getByPlaceholder("Ask Nexus anything, write code, search live web...")
      .fill(text)
    await this.page.keyboard.press("Enter")
  }
}

// ── Stored transcript helpers ────────────────────────────────────────────────
//
// `messagesFromHistory` only treats a row as addressable by the edit/regenerate
// routes when its id is a server UUID, so these fixtures use real UUIDs rather
// than `msg-1`-style handles: with a client id the hooks take the local-only
// path and the PATCH/POST those journeys exist to prove would never be sent.

export const HISTORY_USER_ID = "22222222-2222-4222-8222-222222222222"
export const HISTORY_ASSISTANT_ID = "33333333-3333-4333-8333-333333333333"
export const HISTORY_QUESTION = "what is the capital of France"
export const HISTORY_ANSWER = "Paris is the capital of France."

/** A stored conversation row in the shape `messagesFromHistory` reads. */
export function historyRow(over: Record<string, unknown> = {}) {
  return {
    id: HISTORY_USER_ID,
    role: "user",
    content: HISTORY_QUESTION,
    thought_process: null,
    model: null,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    citations: [],
    tool_calls: [],
    user_feedback: null,
    created_at: "2026-10-21T00:00:00Z",
    ...over,
  }
}

/** One question and its answer, optionally customising the assistant row. */
export function historyTranscript(assistant: Record<string, unknown> = {}) {
  return [
    historyRow(),
    historyRow({
      id: HISTORY_ASSISTANT_ID,
      role: "assistant",
      content: HISTORY_ANSWER,
      model: "openai/gpt-4o-mini",
      ...assistant,
    }),
  ]
}

/**
 * Sidebar + transcript + a chat stream, then open the conversation.
 *
 * The transcript route is registered before the list route on purpose: both
 * match `/api/conversations…` and the mock router is first-match-wins, so the
 * detail route has to come first.
 */
export async function openConversation(
  page: Page,
  messages: unknown[],
  stream: string = `0:${JSON.stringify("A fresh answer.")}\n`
) {
  const mocks = mockApi(page)

  mocks.route(new RegExp(`/api/conversations/${TEST_CONVERSATION.id}$`), (route) => {
    if (route.request().method() === "PATCH") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(messages[messages.length - 1]),
      })
    }
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: TEST_CONVERSATION.id,
        title: TEST_CONVERSATION.title,
        system_prompt: null,
        messages,
      }),
    })
  })

  mocks.route(/\/api\/conversations(\?|$)/, (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(TEST_CONVERSATION),
      })
    }
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([TEST_CONVERSATION]),
    })
  })

  // Editing is a PATCH on the message row. Without this route the catch-all
  // answers 404, the hook throws, and no resend is ever sent.
  mocks.route(new RegExp(`/messages/${HISTORY_USER_ID}$`), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(historyRow({ content: "edited" })),
    })
  )

  mocks.route(/\/api\/chat(\?|$)/, (route) => route.fulfill(buildChatStream(stream)))

  await mocks.setup()
  await page.goto("/app")
  await page.getByText(TEST_CONVERSATION.title).first().click()
  await expect(page.getByText(HISTORY_QUESTION).first()).toBeVisible()
  return mocks
}

export const test = base
export { expect } from "@playwright/test"