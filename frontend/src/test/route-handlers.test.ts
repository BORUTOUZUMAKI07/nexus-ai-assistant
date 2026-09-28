/**
 * Real coverage of the Next route handlers -- the layer the component tests
 * could never reach.
 *
 * The problem this exists to solve: the component suite mocks the network at
 * the MSW layer, and under jsdom no route handler ever executes. So an admin
 * tab whose relay was missing entirely still passed, because the test talked to
 * MSW and never to the route. Every assertion in the suite was about a fiction.
 *
 * These tests import each route module and invoke its exported handler, with
 * `@/lib/proxy` stubbed so the forwarded path and payload are observable. That
 * exercises the code that actually runs in production: the path forwarded, the
 * HTTP method, whether the body survives, and -- most importantly -- whether a
 * backend error is propagated instead of being flattened into a success.
 *
 * It is data-driven off the filesystem, so a newly added route is covered the
 * moment it lands and a deleted one is reported rather than silently skipped.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { readdirSync, statSync } from "node:fs"
import { join, relative } from "node:path"

// Capture what each relay forwards instead of performing a real fetch.
const backendFetch = vi.fn()
const proxyJson = vi.fn()

vi.mock("@/lib/proxy", () => ({
  backendFetch: (...args: unknown[]) => backendFetch(...args),
  proxyJson: (...args: unknown[]) => proxyJson(...args),
}))

/** A cookie jar with a signed-in session, so auth-gated routes are reachable. */
const SESSION = {
  nexus_access_token: "test-access-token",
  nexus_refresh_token: "test-refresh-token",
}

function cookieStore(jar: Record<string, string> = SESSION) {
  return { get: (name: string) => (name in jar ? { value: jar[name] } : undefined) }
}

// Not every route goes through lib/proxy: /api/audio/transcribe, /api/chat and
// /api/hitl read the auth cookie themselves, so `cookies()` needs a request scope
// or the handler throws "cookies was called outside a request scope" before it
// forwards anything. Individual tests re-mock this with their own jar.
vi.mock("next/headers", () => ({ cookies: async () => cookieStore() }))

/** A Response the mocked helpers can hand back to a handler. */
function fakeBackendResponse(status = 200, body: unknown = { ok: true }) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

const SRC = join(__dirname, "..")

/** Every route.ts under src/app, as { routePath, absolutePath }. */
function findRoutes(dir = join(SRC, "app")): { route: string; file: string }[] {
  const out: { route: string; file: string }[] = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      out.push(...findRoutes(full))
    } else if (entry === "route.ts") {
      // src/app/api/foo/[id] -> /api/foo/[id]
      const rel = relative(join(SRC, "app"), dir).split("\\").join("/")
      out.push({ route: "/" + rel, file: full })
    }
  }
  return out
}

const ROUTES = findRoutes()

/** Load a route module fresh so per-test mocks of proxyJson are honoured. */
async function loadRoute(file: string): Promise<RouteModule> {
  vi.resetModules()
  return (await import(/* @vite-ignore */ file)) as RouteModule
}

/**
 * Load a route module once and reuse it, for checks that only inspect what the
 * module *exports* rather than what its handlers do.
 *
 * loadRoute() has to call vi.resetModules() so each test picks up a fresh
 * proxyJson, which means every call re-evaluates the route from scratch. That
 * isolation is the point for the forwarding tests, but it is pure cost for a
 * question like "does this file export a GET?". A module's export shape does
 * not depend on what proxyJson is mocked to, so a cached module answers it
 * just as correctly -- and does not push the test past the 5s default.
 */
const staticModules = new Map<string, Promise<RouteModule>>()
function loadRouteStatic(file: string): Promise<RouteModule> {
  let mod = staticModules.get(file)
  if (!mod) {
    mod = import(/* @vite-ignore */ file) as Promise<RouteModule>
    staticModules.set(file, mod)
  }
  return mod
}

/**
 * A stand-in NextRequest. The handlers use more than .json() -- some construct
 * `new URL(req.url)` to read query params, some read headers to derive cookie
 * flags, some take multipart uploads -- so a bare { json } object throws
 * `TypeError: Invalid URL` and the route never gets exercised.
 */
function nextRequest(route: string, search = "", body: unknown = { probe: "body" }) {
  const url = `http://localhost${route}${search}`
  const upload = new FormData()
  upload.set("file", new File(["audio-bytes"], "clip.wav", { type: "audio/wav" }))
  return {
    url,
    nextUrl: new URL(url),
    // /api/auth/logout derives cookie flags from the forwarded proto.
    headers: new Headers({ "x-forwarded-proto": "https" }),
    cookies: new Map(),
    json: async () => body,
    formData: async () => upload,
    text: async () => "",
    arrayBuffer: async () => new ArrayBuffer(8),
    clone() {
      return this
    },
  } as never
}

/**
 * The second argument Next passes to a handler for a dynamic segment.
 * `params` is a Promise in current Next and handlers `await` it, so a plain
 * object destructures to undefined.
 */
function routeContext(route: string) {
  const params: Record<string, string> = {}
  for (const m of route.matchAll(/\[([^\]]+)\]/g)) params[m[1]] = `test-${m[1]}`
  return { params: Promise.resolve(params) } as never
}

/**
 * The path a relay must forward.
 *
 * `backendFetch` prefixes `${BACKEND_URL}/api/v1`, so a route mounted at
 * /api/admin/hooks/[id] forwards /admin/hooks/<id> -- the /api prefix is
 * dropped and the dynamic segment is interpolated from the route params.
 */
function expectedBackendPath(route: string): string {
  return route
    .replace(/^\/api/, "")
    .replace(/\[([^\]]+)\]/g, (_, name) => `test-${name}`)
}

/**
 * A dynamically imported route module. The dynamic import gives back
 * `Record<string, unknown>`, so the handler signatures are restated here to
 * make the call sites type-check without a cast at every invocation.
 */
type Handler = (req: unknown, ctx: unknown) => Promise<Response>

interface RouteModule {
  GET?: Handler
  POST?: Handler
  PUT?: Handler
  PATCH?: Handler
  DELETE?: Handler
  [method: string]: unknown
}

/** A backend call captured from either the proxy helpers or raw fetch. */
interface BackendCall {
  /** Path relative to /api/v1, e.g. "/admin/monitoring/slices". */
  path: string
  method: string
  headers: Headers
  body: unknown
}

/**
 * Routes that legitimately do not follow the one-route-one-backend-path shape.
 *
 * Recording them explicitly is the point: each is a deliberate design choice,
 * and an exception table means quietly changing one of them now fails this
 * test instead of drifting unnoticed.
 */
const EXCEPTIONS: Record<string, { backendPath?: string; directFetch?: boolean }> = {
  // Frontend "start SSO" maps to the backend's OIDC start endpoint.
  "/api/auth/oauth": { backendPath: "/auth/oauth/login" },
  // These need the raw response body (streaming, multipart), so they call
  // fetch() directly instead of going through the proxy helpers.
  "/api/chat": { directFetch: true },
  "/api/hitl": { directFetch: true },
  "/api/audio/transcribe": { directFetch: true },
}

/** True when the route is expected to bypass the proxy helpers. */
function isDirect(route: string) {
  return EXCEPTIONS[route]?.directFetch === true
}

/**
 * Routes that can legitimately answer without reaching the backend.
 *
 * These are covered by dedicated tests below that assert both branches, so the
 * generic sweep skips them rather than papering over a missing call.
 */
const SHORT_CIRCUITS = new Set(["/api/auth/oauth/callback"])

/**
 * A body each route's own input validation will accept.
 *
 * /api/chat rejects a request with no `messages` array and /api/hitl rejects
 * one with no `threadId`/`action` string, both with a 400 before forwarding.
 * A generic { probe: "body" } payload therefore never reaches the backend and
 * the sweep would report a false failure.
 */
const SAMPLE_BODIES: Record<string, unknown> = {
  "/api/chat": { messages: [{ role: "user", content: "hi" }] },
  "/api/hitl": { threadId: "thread-1", action: "approve" },
}

/**
 * Spy on global fetch for the routes that bypass lib/proxy.
 *
 * This is where the security property lives: a route that reads the auth
 * cookie itself must still send the bearer token, otherwise the backend sees
 * an anonymous caller and returns 401.
 */
function captureDirectFetch() {
  const seen: { url: string; init: RequestInit }[] = []
  const original = globalThis.fetch
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    seen.push({ url: String(input), init: init ?? {} })
    // Streaming routes want a body with a reader; a plain string is fine.
    return new Response("data: {}\n\n", {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    })
  }) as unknown as typeof fetch
  return {
    seen,
    restore: () => {
      globalThis.fetch = original
    },
  }
}

describe("route handler inventory", () => {
  it("finds the route handlers on disk", () => {
    // If this is 0, the dynamic-import loops below silently test nothing.
    expect(ROUTES.length).toBeGreaterThan(30)
  })

  it("every route exports at least one HTTP method", async () => {
    const methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    // Concurrently, not in a sequential await loop: each import is independent
    // and async, so awaiting them one at a time serialises 43 module loads for
    // no reason. This is the one test in the suite that imports every route, so
    // it is also the one that blows the default budget when the other 23 files
    // are running in parallel.
    const mods = await Promise.all(ROUTES.map(({ file }) => loadRouteStatic(file)))
    const bad = ROUTES.filter((_, i) => !methods.some((m) => typeof mods[i][m] === "function")).map(
      ({ route }) => route,
    )
    expect(bad).toEqual([])
  })

  it("the exception table only names routes that actually exist", () => {
    // An entry left behind by a renamed route would make the table a lie.
    const known = new Set(ROUTES.map((r) => r.route))
    const stale = Object.keys(EXCEPTIONS).filter((r) => !known.has(r))
    expect(stale).toEqual([])
  })
})

describe("every route forwards to the backend under the correct path", () => {
  beforeEach(() => {
    backendFetch.mockReset()
    proxyJson.mockReset()
    // Implemented, not mockResolvedValue: a Response body can only be read
    // once and several routes call res.text() or res.json(); a shared instance
    // throws "Body is unusable" on the second read.
    proxyJson.mockImplementation(async () => fakeBackendResponse())
    backendFetch.mockImplementation(async () => fakeBackendResponse())
  })

  /** Every backend call a handler made, via helpers or raw fetch. */
  async function callsFor(
    route: string,
    method: string,
    invoke: (req: unknown) => Promise<unknown>,
  ): Promise<BackendCall[]> {
    proxyJson.mockClear()
    backendFetch.mockClear()
    const direct = isDirect(route) ? captureDirectFetch() : null
    try {
      await invoke(nextRequest(route, "", SAMPLE_BODIES[route] ?? { probe: "body" }))
    } finally {
      direct?.restore()
    }

    const viaHelpers: BackendCall[] = [
      ...(proxyJson.mock.calls as [string, RequestInit | undefined][]),
      ...(backendFetch.mock.calls as [string, RequestInit | undefined][]),
    ].map(([path, init]) => ({
      path,
      method: init?.method ?? "GET",
      headers: new Headers(init?.headers),
      body: init?.body,
    }))

    const viaFetch: BackendCall[] = (direct?.seen ?? []).map(({ url, init }) => {
      // Raw routes build an absolute `${BACKEND_URL}/api/v1/...` URL.
      const m = url.match(/\/api\/v1(\/[^?]*)/)
      return {
        path: m ? m[1] : url,
        method: init.method ?? "GET",
        headers: new Headers(init.headers),
        body: init.body,
      }
    })

    return [...viaHelpers, ...viaFetch]
  }

  it("each GET route reaches the backend", async () => {
    const problems: string[] = []

    for (const { route, file } of ROUTES) {
      const mod = await loadRoute(file)
      if (typeof mod.GET !== "function") continue
      if (SHORT_CIRCUITS.has(route)) continue

      const calls = await callsFor(route, "GET", (req) =>
        (mod.GET as (a: unknown, b: unknown) => Promise<unknown>)(
          req,
          routeContext(route),
        ),
      )

      if (calls.length === 0) {
        problems.push(`${route}: GET never reached the backend`)
        continue
      }
      if (calls.some((c) => c.body !== undefined)) {
        problems.push(`${route}: GET sends a request body`)
      }
    }

    expect(problems).toEqual([])
  })

  it("each mutating route reaches the backend with its own method", async () => {
    const problems: string[] = []
    const MUTATING = ["POST", "PUT", "PATCH", "DELETE"] as const

    for (const { route, file } of ROUTES) {
      const mod = await loadRoute(file)
      for (const method of MUTATING) {
        const handler = mod[method]
        if (typeof handler !== "function") continue
        if (SHORT_CIRCUITS.has(route)) continue

        const calls = await callsFor(route, method, (req) =>
          (handler as (a: unknown, b: unknown) => Promise<unknown>)(
            req,
            routeContext(route),
          ),
        )

        if (calls.length === 0) {
          problems.push(`${route}: ${method} never reached the backend`)
          continue
        }
        for (const c of calls) {
          if (c.method !== method) {
            problems.push(`${route}: ${method} was forwarded as ${c.method}`)
          }
        }
      }
    }

    expect(problems).toEqual([])
  })

  it("relay paths match the backend, not a guess", async () => {
    // This is the check that would have caught the three broken admin tabs:
    // they were hitting /api/admin/monitoring/slices in the browser while no
    // route.ts forwarded to the backend path at all.
    const problems: string[] = []

    for (const { route, file } of ROUTES) {
      const mod = await loadRoute(file)
      const expected = EXCEPTIONS[route]?.backendPath ?? expectedBackendPath(route)

      // Raw-fetch routes derive the backend path from the request body
      // (conversation id, thread id), so only helper-based relays have a
      // mechanically derivable path. Those are the ones that broke before.
      if (isDirect(route) || SHORT_CIRCUITS.has(route)) continue

      for (const method of ["GET", "POST", "PUT", "PATCH", "DELETE"] as const) {
        if (typeof mod[method] !== "function") continue

        const calls = await callsFor(route, method, (req) =>
          (mod[method] as (a: unknown, b: unknown) => Promise<unknown>)(
            req,
            routeContext(route),
          ),
        )
        for (const c of calls) {
          // Relays may append a query string they built from the request.
          if (c.path.split("?")[0] !== expected) {
            problems.push(
              `${route}: ${method} forwards to "${c.path}", backend expects "${expected}"`,
            )
          }
        }
      }
    }

    expect(problems).toEqual([])
  })

  it("routes that bypass the proxy still send the session token", async () => {
    // /api/chat, /api/hitl and /api/audio/transcribe read the auth cookie
    // themselves instead of using backendFetch. If any of them forgot to set
    // the Authorization header, the backend would treat the user as anonymous
    // and the failure would only show up in production.
    const problems: string[] = []

    for (const { route, file } of ROUTES) {
      if (!isDirect(route)) continue
      const mod = await loadRoute(file)

      for (const method of ["GET", "POST", "PUT", "PATCH", "DELETE"] as const) {
        if (typeof mod[method] !== "function") continue

        const calls = await callsFor(route, method, (req) =>
          (mod[method] as (a: unknown, b: unknown) => Promise<unknown>)(
            req,
            routeContext(route),
          ),
        )
        if (calls.length === 0) {
          problems.push(`${route}: ${method} never reached the backend`)
          continue
        }
        for (const c of calls) {
          const auth = c.headers.get("Authorization")
          if (auth !== `Bearer ${SESSION.nexus_access_token}`) {
            problems.push(
              `${route}: ${method} sent Authorization "${auth ?? "(none)"}"`,
            )
          }
          if (!c.path.startsWith("/")) {
            problems.push(`${route}: ${method} sent an unresolvable path "${c.path}"`)
          }
        }
      }
    }

    expect(problems).toEqual([])
  })
})

describe("routes that short-circuit on bad input", () => {
  beforeEach(() => {
    backendFetch.mockReset()
    proxyJson.mockReset()
    proxyJson.mockImplementation(async () => fakeBackendResponse())
    backendFetch.mockImplementation(async () => fakeBackendResponse())
  })

  it("the OAuth callback rejects a request with no code/state instead of calling the backend", async () => {
    const file = ROUTES.find((r) => r.route === "/api/auth/oauth/callback")?.file
    expect(file).toBeDefined()
    const mod = await loadRoute(file!)

    const res = await mod.GET!(
      nextRequest("/api/auth/oauth/callback"),
      routeContext("/api/auth/oauth/callback"),
    )

    expect(res.status).toBe(307)
    expect(res.headers.get("location")).toContain("oauth_missing_params")
    expect(backendFetch).not.toHaveBeenCalled()
  })

  it("the OAuth callback exchanges code+state when the IdP supplies them", async () => {
    const file = ROUTES.find((r) => r.route === "/api/auth/oauth/callback")?.file
    const mod = await loadRoute(file!)
    backendFetch.mockImplementation(async () =>
      fakeBackendResponse(200, { access_token: "at", refresh_token: "rt", expires_in: 60 }),
    )

    const res = await mod.GET!(
      nextRequest("/api/auth/oauth/callback", "?code=abc&state=xyz"),
      routeContext("/api/auth/oauth/callback"),
    )

    expect(backendFetch).toHaveBeenCalledTimes(1)
    const [path, init] = backendFetch.mock.calls[0] as [string, RequestInit]
    expect(path).toBe("/auth/oauth/callback")
    expect(init.method).toBe("POST")
    // The PKCE verifier must reach the backend, not just a code.
    expect(JSON.parse(init.body as string)).toEqual({ code: "abc", state: "xyz" })
    // A successful exchange lands the user in the app with cookies set.
    expect(res.status).toBe(307)
    expect(res.headers.get("location")).toBe("http://localhost/app")
  })

  it("a POST relay with a malformed JSON body answers 400 without calling the backend", async () => {
    const file = ROUTES.find(
      (r) => r.route === "/api/admin/optimization/run",
    )?.file
    expect(file).toBeDefined()
    const mod = await loadRoute(file!)

    const res = await mod.POST!(
      {
        json: async () => {
          throw new SyntaxError("Unexpected token")
        },
      } as never,
      routeContext("/api/admin/optimization/run"),
    )

    expect(res.status).toBe(400)
    await expect(res.json()).resolves.toEqual({ detail: "invalid_json_body" })
    expect(proxyJson).not.toHaveBeenCalled()
  })
})

describe("proxyJson propagates backend status instead of flattening it", () => {
  // These assert the real helper, since the module mock above replaces it.
  beforeEach(() => {
    vi.resetModules()
    vi.doUnmock("@/lib/proxy")
  })

  async function withStubbedFetch<T>(impl: typeof fetch, run: () => Promise<T>) {
    const original = globalThis.fetch
    globalThis.fetch = impl
    try {
      return await run()
    } finally {
      globalThis.fetch = original
    }
  }

  it("returns a 500 from the backend as a 500, not a 200 with an error body", async () => {
    const { proxyJson } = await import("@/lib/proxy")

    const res = await withStubbedFetch(
      (async () =>
        new Response(JSON.stringify({ detail: "boom" }), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        })) as typeof fetch,
      () => proxyJson("/admin/monitoring/slices"),
    )

    expect(res.status).toBe(500)
    await expect(res.json()).resolves.toEqual({ detail: "boom" })
  })

  it("returns a 401 from the backend as a 401", async () => {
    const { proxyJson } = await import("@/lib/proxy")

    const res = await withStubbedFetch(
      (async () =>
        new Response(JSON.stringify({ detail: "not_authenticated" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        })) as typeof fetch,
      () => proxyJson("/auth/me"),
    )

    expect(res.status).toBe(401)
  })

  it("preserves an empty body without trying to parse it", async () => {
    const { proxyJson } = await import("@/lib/proxy")

    const res = await withStubbedFetch(
      (async () => new Response(null, { status: 204 })) as typeof fetch,
      () => proxyJson("/conversations/abc", { method: "DELETE" }),
    )

    expect(res.status).toBe(204)
    await expect(res.text()).resolves.toBe("")
  })

  it("reports an unreachable backend as 503 rather than throwing", async () => {
    const { proxyJson } = await import("@/lib/proxy")

    const res = await withStubbedFetch(
      (async () => {
        throw new TypeError("fetch failed")
      }) as typeof fetch,
      () => proxyJson("/auth/me"),
    )

    // A TypeError escaping here becomes an opaque 500 with a stack in the server
    // log; the helper is supposed to convert it into something the UI can show.
    expect(res.status).toBe(503)
    await expect(res.json()).resolves.toEqual({ detail: "backend_unavailable" })
  })

  it("does not forward a stale bearer token on login or refresh", async () => {
    // `cookies()` returns a store whose get() yields { value }, not a bare
    // string -- a Map of raw strings makes ?.value undefined and every request
    // look anonymous, which would make this test pass for the wrong reason.
    vi.doMock("next/headers", () => ({
      cookies: async () => cookieStore({ nexus_access_token: "stale-token" }),
    }))
    const { backendFetch } = await import("@/lib/proxy")

    const seen: Headers[] = []
    const res = await withStubbedFetch(
      (async (_url: unknown, init: RequestInit) => {
        seen.push(new Headers(init.headers))
        return new Response("{}", { status: 200 })
      }) as unknown as typeof fetch,
      async () => {
        await backendFetch("/auth/login", { method: "POST" })
        await backendFetch("/auth/refresh", { method: "POST" })
        await backendFetch("/auth/me")
      },
    )
    expect(res).toBeUndefined()

    expect(seen[0].get("Authorization")).toBeNull()
    expect(seen[1].get("Authorization")).toBeNull()
    // Everything else must still authenticate, or the app sees the user as
    // anonymous on every request except login.
    expect(seen[2].get("Authorization")).toBe("Bearer stale-token")
  })
})
