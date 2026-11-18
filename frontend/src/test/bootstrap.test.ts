/**
 * Contract tests for the additive BFF bootstrap route.
 *
 * The client already issues `/api/auth/me`, `/api/conversations` and
 * `/api/settings` in parallel. The honest win here is collapsing three
 * browser <-> Vercel round trips into one, with one shared server-side
 * auth/refresh path — NOT turning a serial sequence into a parallel one.
 *
 * The proxy helpers are mocked (as in route-handlers.test.ts) so call order and
 * call paths are observable without a live backend.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

const backendFetch = vi.fn();
const proxyJson = vi.fn();

vi.mock("@/lib/proxy", () => ({
  backendFetch: (...args: unknown[]) => backendFetch(...args),
  proxyJson: (...args: unknown[]) => proxyJson(...args),
}));

const COOKIES: Record<string, string> = {
  nexus_access_token: "test-access",
  nexus_refresh_token: "test-refresh",
};

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (name in COOKIES ? { value: COOKIES[name] } : undefined),
  }),
  headers: async () => ({ get: () => undefined }),
}));

const USER = {
  id: "u-1",
  email: "user@example.com",
  role: "user",
  is_active: true,
};
const CONVERSATIONS = {
  items: [
    {
      id: "c-1",
      title: "Hello",
      model: "complex_reasoning",
      is_pinned: false,
      is_archived: false,
      project_id: null,
      token_count: 0,
      created_at: "2026-11-18T10:00:00Z",
      updated_at: "2026-11-18T10:00:00Z",
    },
  ],
  next_cursor: null,
  has_more: false,
};
/** Page envelope the browser sees (same normalisation as fetchConversations). */
const PAGE = {
  ...CONVERSATIONS,
  total: CONVERSATIONS.items.length,
  page: 1,
  size: 50,
};
const SETTINGS = {
  id: "s-1",
  user_id: "u-1",
  theme: "dark",
  default_model: "complex_reasoning",
  system_prompt_override: null,
  temperature: 0.7,
  max_tokens: 4096,
  stream_response: true,
  enable_memory: true,
  enable_tools: true,
  custom_settings: {},
};

function ok(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const REQUEST = {
  url: "http://localhost/api/bootstrap?limit=50&offset=0&archived=false",
  headers: new Headers({ "x-forwarded-proto": "https" }),
} as never;

type RouteModule = {
  GET?: (request: never) => Promise<Response>;
  dynamic?: string;
};

async function loadRoute(): Promise<RouteModule> {
  vi.resetModules();
  return (await import("@/app/api/bootstrap/route")) as RouteModule;
}

/** Healthy backend, used by every test that is not about a specific failure. */
function healthyBackend() {
  backendFetch.mockReset();
  proxyJson.mockReset();
  backendFetch.mockImplementation(async (path: string) => {
    if (path === "/auth/me") return ok(USER);
    if (path === "/auth/refresh") return ok({ access_token: "fresh" });
    if (path === "/conversations" || path.startsWith("/conversations?")) {
      return ok(CONVERSATIONS);
    }
    if (path === "/settings") return ok(SETTINGS);
    throw new Error(`unexpected backendFetch path: ${path}`);
  });
  proxyJson.mockImplementation(async (path: string) => {
    if (path.startsWith("/conversations")) return ok(CONVERSATIONS);
    if (path === "/settings") return ok(SETTINGS);
    throw new Error(`unexpected proxyJson path: ${path}`);
  });
}

describe("GET /api/bootstrap", () => {
  beforeEach(healthyBackend);

  it("starts all three backend calls before any of them resolves", async () => {
    const gates = {
      auth: deferred<Response>(),
      conversations: deferred<Response>(),
      settings: deferred<Response>(),
    };
    backendFetch.mockImplementation(() => gates.auth.promise);
    proxyJson.mockImplementation((path: string) =>
      path.startsWith("/conversations")
        ? gates.conversations.promise
        : gates.settings.promise,
    );

    const mod = await loadRoute();
    expect(mod.GET).toBeTypeOf("function");
    // Deliberately NOT awaited: the assertion below is about what has started
    // while every upstream promise is still pending.
    const pending = mod.GET!(REQUEST);

    expect(backendFetch).toHaveBeenCalledTimes(1);
    expect(backendFetch.mock.calls[0][0]).toBe("/auth/me");
    expect(proxyJson).toHaveBeenCalledTimes(2);
    expect(proxyJson.mock.calls.map((call) => call[0])).toEqual([
      "/conversations?limit=50&offset=0&archived=false",
      "/settings",
    ]);

    gates.auth.resolve(ok(USER));
    gates.conversations.resolve(ok(CONVERSATIONS));
    gates.settings.resolve(ok(SETTINGS));
    const res = await pending;
    expect(res.status).toBe(200);
  });

  it("returns one flat object matching the three existing route shapes", async () => {
    const mod = await loadRoute();
    const res = await mod.GET!(REQUEST);

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      authenticated: true,
      user: USER,
      conversations: PAGE,
      settings: SETTINGS,
    });
  });

  it("normalises a legacy plain-array conversation list into the page envelope", async () => {
    proxyJson.mockImplementation(async (path: string) =>
      path.startsWith("/conversations") ? ok(CONVERSATIONS.items) : ok(SETTINGS),
    );
    const mod = await loadRoute();
    const res = await mod.GET!(REQUEST);

    expect((await res.json()).conversations).toEqual(PAGE);
  });

  it("derives page/size from offset/limit for the forward-compatible envelope", async () => {
    const request = {
      url: "http://localhost/api/bootstrap?limit=25&offset=50&archived=true",
      headers: new Headers(),
    } as never;
    proxyJson.mockImplementation(async (path: string) => {
      if (path.startsWith("/conversations")) {
        expect(path).toBe("/conversations?limit=25&offset=50&archived=true");
        return ok({ ...CONVERSATIONS, has_more: true, next_cursor: "cursor-1" });
      }
      return ok(SETTINGS);
    });
    const mod = await loadRoute();
    const res = await mod.GET!(request);

    expect((await res.json()).conversations).toEqual({
      ...PAGE,
      page: 3,
      size: 25,
      has_more: true,
      next_cursor: "cursor-1",
    });
  });

  it("reports a rejected session as exactly { authenticated: false }", async () => {
    // No refresh cookie means the BFF must not start a second refresh leg.
    COOKIES.nexus_refresh_token = "";
    backendFetch.mockImplementation(async () => ok({ detail: "not_authenticated" }, 401));
    const mod = await loadRoute();
    try {
      const res = await mod.GET!(REQUEST);
      expect(await res.json()).toEqual({ authenticated: false });
    } finally {
      COOKIES.nexus_refresh_token = "test-refresh";
    }
  });

  it("degrades auth_unavailable without claiming the user is signed out", async () => {
    backendFetch.mockImplementation(async (path: string) =>
      path === "/auth/me" ? ok({ detail: "backend_unavailable" }, 503) : ok({}),
    );
    const mod = await loadRoute();
    const res = await mod.GET!(REQUEST);

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      authenticated: false,
      unavailable: true,
      detail: "auth_unavailable",
      upstream_status: 503,
    });
    // Parity with /api/auth/me's cold-start hint.
    expect(res.headers.get("Retry-After")).toBe("2");
  });

  it("degrades one failing subsystem without losing the other two", async () => {
    proxyJson.mockImplementation(async (path: string) => {
      if (path.startsWith("/conversations")) return ok({ detail: "boom" }, 500);
      return ok(SETTINGS);
    });
    const mod = await loadRoute();
    const res = await mod.GET!(REQUEST);

    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.authenticated).toBe(true);
    expect(body.user).toEqual(USER);
    expect(body.conversations).toEqual({
      ...PAGE,
      items: [],
      total: 0,
      unavailable: true,
      detail: "conversations_unavailable",
      upstream_status: 500,
    });
    expect(body.settings).toEqual(SETTINGS);
  });

  it("degrades a thrown backend error without leaking its message", async () => {
    proxyJson.mockImplementation(async (path: string) => {
      if (path === "/settings") throw new Error("secret-token-value leaked here");
      return ok(CONVERSATIONS);
    });
    const mod = await loadRoute();
    const res = await mod.GET!(REQUEST);
    const raw = await res.text();

    expect(raw).not.toContain("secret-token-value");
    expect(JSON.parse(raw).settings).toEqual({
      unavailable: true,
      detail: "settings_unavailable",
    });
  });

  it("heals an expired access token and retries the other subsystems with it", async () => {
    backendFetch
      .mockResolvedValueOnce(ok({ detail: "not_authenticated" }, 401))
      .mockResolvedValueOnce(
        ok({
          access_token: "fresh-access",
          refresh_token: "rotated-refresh",
          expires_in: 123,
          refresh_expires_in: 456,
        }),
      )
      .mockResolvedValueOnce(ok(USER));
    // The parallel phase sees the stale token and is rejected everywhere else.
    proxyJson.mockImplementation(async () => ok({ detail: "unauthorized" }, 401));
    backendFetch.mockImplementation(async (path: string) => {
      if (path.startsWith("/conversations")) return ok(CONVERSATIONS);
      if (path === "/settings") return ok(SETTINGS);
      return ok(USER);
    });

    const mod = await loadRoute();
    const res = await mod.GET!(REQUEST);
    const body = await res.json();

    expect(body.authenticated).toBe(true);
    expect(body.user).toEqual(USER);
    expect(body.conversations).toEqual(PAGE);
    expect(body.settings).toEqual(SETTINGS);
    // Cookies-only: the JSON reports *that* cookies were re-set, never values.
    expect(body.refreshed).toEqual({
      access: true,
      refresh: true,
      expires_in: 123,
      refresh_expires_in: 456,
    });
    expect(JSON.stringify(body)).not.toContain("fresh-access");
    expect(JSON.stringify(body)).not.toContain("rotated-refresh");

    const retried = backendFetch.mock.calls.filter(
      (call) => call[0].startsWith("/conversations") || call[0] === "/settings",
    );
    expect(retried.length).toBeGreaterThan(0);
    expect(retried.every((call) => call[2]?.accessToken === "fresh-access")).toBe(true);

    const cookies = [
      ...(res.headers.getSetCookie?.() ?? []),
      res.headers.get("set-cookie") ?? "",
    ].filter(Boolean);
    expect(cookies.some((header) => header.startsWith("nexus_access_token="))).toBe(true);
    expect(cookies.some((header) => header.startsWith("nexus_refresh_token="))).toBe(true);
  });

  it("is explicitly dynamic and never cacheable", async () => {
    const mod = await loadRoute();
    const res = await mod.GET!(REQUEST);

    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect((mod as { dynamic?: string }).dynamic).toBe("force-dynamic");
  });
});

describe("bootstrapQueryOptions", () => {
  it("builds a fresh, non-shared query option for the shell", async () => {
    const { bootstrapQueryOptions } = await import("@/lib/bootstrap-query");
    const options = bootstrapQueryOptions({ limit: 25, offset: 5, archived: true });

    expect(options.queryKey).toEqual([
      "bootstrap",
      { limit: 25, offset: 5, archived: true },
    ]);
    expect(options.staleTime).toBeGreaterThan(0);
    expect(options.refetchOnWindowFocus).toBe(false);

    const original = globalThis.fetch;
    const seen: { url: string; init?: RequestInit }[] = [];
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      seen.push({ url: String(input), init });
      return ok({
        authenticated: true,
        user: USER,
        conversations: PAGE,
        settings: SETTINGS,
      });
    }) as typeof fetch;
    try {
      const data = (await options.queryFn!({} as never)) as {
        settings: typeof SETTINGS;
      };
      expect(data.settings).toEqual(SETTINGS);
    } finally {
      globalThis.fetch = original;
    }

    expect(seen).toHaveLength(1);
    expect(seen[0].url).toBe("/api/bootstrap?limit=25&offset=5&archived=true");
    expect(seen[0].init?.cache).toBe("no-store");
  });
});
