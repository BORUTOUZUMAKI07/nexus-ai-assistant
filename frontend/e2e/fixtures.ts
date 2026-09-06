import { test as base, type Page, type Route } from "@playwright/test"

export const AUTH_COOKIE = "nexus_access_token"
export const TEST_TOKEN = "test-access-token"

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
      body: JSON.stringify({
        access_token: TEST_TOKEN,
        refresh_token: "test-refresh-token",
        token_type: "bearer",
      }),
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
    await this.page.goto("/")
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

export const test = base
export { expect } from "@playwright/test"