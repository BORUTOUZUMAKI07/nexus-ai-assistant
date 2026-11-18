/**
 * Additive BFF bootstrap: one client round trip for auth + conversations +
 * settings. The three original routes are left intact and unchanged.
 *
 * Next.js notes (installed docs):
 * - `01-app/01-getting-started/15-route-handlers.md`: GET handlers are dynamic
 *   by default; accessing `cookies()`/`request.url` opts out of static
 *   rendering, and `Cache-Control: no-store` is set explicitly below.
 * - `01-app/03-api-reference/03-file-conventions/route.md`: `cookies()` must be
 *   awaited in Next 16; `request.nextUrl.searchParams` / `new URL(request.url)`
 *   is the supported way to read query parameters.
 * - `01-app/02-guides/backend-for-frontend.md`: aggregate several backend
 *   calls in one Route Handler, send only the credentials the backend needs,
 *   return a clean 503/structured error rather than leaking upstream details,
 *   and never let a shared cache serve authenticated responses.
 *
 * The three upstreams are started in the SAME tick via `Promise.allSettled`,
 * so the handler's latency is the slowest of the three, not their sum.
 */
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { backendFetch, proxyJson } from "@/lib/proxy";
import {
  ACCESS_TOKEN_MAX_AGE,
  REFRESH_COOKIE,
  REFRESH_TOKEN_MAX_AGE,
  TOKEN_COOKIE,
  authCookieOptions,
} from "@/lib/auth";
import {
  DEFAULT_BOOTSTRAP_PARAMS,
  type BootstrapAuth,
  type BootstrapConversationPage,
  type BootstrapParams,
  type BootstrapResponse,
  type BootstrapSettings,
} from "@/lib/bootstrap-types";

// Belt-and-braces: the handler reads cookies and the request URL, so Next
// already treats it as dynamic. Explicit config keeps a future refactor from
// accidentally allowing a shared cache to serve one user's session to another.
export const dynamic = "force-dynamic";
export const revalidate = 0;

type AuthUser = Extract<BootstrapAuth, { authenticated: true }>["user"];

interface RefreshedTokens {
  accessToken: string;
  expiresIn?: number;
  refreshToken?: string;
  refreshExpiresIn?: number;
}

interface AuthOutcome {
  result: BootstrapAuth;
  refreshed?: RefreshedTokens;
}

interface SubResult<T> {
  value: T;
  status: number;
}

function json(body: BootstrapResponse, extraHeaders: Record<string, string> = {}) {
  return NextResponse.json(body, {
    status: 200,
    headers: { "Cache-Control": "no-store", ...extraHeaders },
  });
}

async function readJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    // A malformed body is not a verdict about the user's session.
    return null;
  }
}

function settledValue<T>(result: PromiseSettledResult<T>, fallback: T): T {
  return result.status === "fulfilled" ? result.value : fallback;
}

/** FastAPI's own Query constraints; out-of-range input falls back to defaults. */
function parseParams(request: NextRequest): Required<BootstrapParams> {
  const { searchParams } = new URL(request.url);
  const integer = (name: string, fallback: number, min: number, max: number) => {
    const raw = searchParams.get(name);
    if (raw === null) return fallback;
    const value = Number(raw);
    if (!Number.isFinite(value)) return fallback;
    return Math.min(max, Math.max(min, Math.trunc(value)));
  };
  const archived = searchParams.get("archived");
  return {
    limit: integer("limit", DEFAULT_BOOTSTRAP_PARAMS.limit, 1, 100),
    offset: integer("offset", DEFAULT_BOOTSTRAP_PARAMS.offset, 0, 1_000_000),
    archived: archived === "true" || archived === "1",
  };
}

/**
 * Preserve `/api/auth/me`'s cold-start semantics verbatim: "cannot verify" is
 * not "signed out". The client's `SessionProbe.unavailable` flag is carried in
 * the body because this aggregate endpoint must stay a 200 so the other two
 * sub-results remain usable.
 */
function unavailableAuth(upstreamStatus: number): BootstrapAuth {
  return {
    authenticated: false,
    unavailable: true,
    detail: "auth_unavailable",
    upstream_status: upstreamStatus,
  };
}

function unavailableConversations(
  params: Required<BootstrapParams>,
  upstreamStatus?: number,
): BootstrapConversationPage {
  return {
    items: [],
    total: 0,
    page: Math.floor(params.offset / params.limit) + 1,
    size: params.limit,
    next_cursor: null,
    has_more: false,
    unavailable: true,
    detail: "conversations_unavailable",
    ...(upstreamStatus === undefined ? {} : { upstream_status: upstreamStatus }),
  };
}

function unavailableSettings(upstreamStatus?: number): BootstrapSettings {
  return {
    unavailable: true,
    detail: "settings_unavailable",
    ...(upstreamStatus === undefined ? {} : { upstream_status: upstreamStatus }),
  } as BootstrapSettings;
}

/**
 * Minimal copy of the refresh healing in `app/api/auth/me/route.ts`.
 *
 * That route's helper is module-private and returns a full `NextResponse`, so
 * sharing it would require editing a file owned by another owner. The logic
 * below is deliberately the same: one refresh attempt, a 503/5xx is still
 * `auth_unavailable`, 401/403 is a real verdict, and the NEW token is used for
 * the verification probe (the browser has not received it yet). The only
 * difference is that it returns tokens instead of a Response, so cookies can be
 * applied to the single aggregate response at the end.
 */
async function healExpiredAccessToken(): Promise<AuthOutcome | null> {
  const refreshToken = (await cookies()).get(REFRESH_COOKIE)?.value;
  if (!refreshToken) return null;

  const refreshed = await backendFetch("/auth/refresh", {
    method: "POST",
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (refreshed.status >= 500) return { result: unavailableAuth(refreshed.status) };
  if (!refreshed.ok) return null;

  const data = (await readJson(refreshed)) as {
    access_token?: string;
    refresh_token?: string;
    expires_in?: number;
    refresh_expires_in?: number;
  } | null;
  if (!data?.access_token) return null;

  // Verify the token we just minted, not the stale one still in the cookie.
  const probe = await backendFetch(
    "/auth/me",
    { method: "GET" },
    { accessToken: data.access_token },
  );
  if (probe.status >= 500) return { result: unavailableAuth(probe.status) };
  if (probe.status !== 200) return null;

  return {
    result: { authenticated: true, user: (await readJson(probe)) as AuthUser },
    refreshed: {
      accessToken: data.access_token,
      expiresIn: data.expires_in,
      refreshToken: data.refresh_token,
      refreshExpiresIn: data.refresh_expires_in,
    },
  };
}

async function resolveAuth(): Promise<AuthOutcome> {
  try {
    const res = await backendFetch("/auth/me", { method: "GET" });
    if (res.status === 200) {
      return { result: { authenticated: true, user: (await readJson(res)) as AuthUser } };
    }
    if (res.status === 401 || res.status === 403) {
      const healed = await healExpiredAccessToken();
      return healed ?? { result: { authenticated: false } };
    }
    return { result: unavailableAuth(res.status) };
  } catch {
    // backendFetch normally converts transport failures into a 503 itself; a
    // throw here is unexpected, but must still never be read as "signed out".
    return { result: unavailableAuth(503) };
  }
}

/**
 * `/conversations` returns the keyset envelope or, for backward compatibility,
 * a plain array. Normalise both to the page envelope the client already builds:
 * total/page/size are derived from the returned items and the request params,
 * using only field names that already exist on the browser contract.
 */
function normalizeConversations(
  data: unknown,
  params: Required<BootstrapParams>,
): BootstrapConversationPage | null {
  // page/size/total mirror the client's fetchConversations normalisation; the
  // future single-backend call will return this same envelope unchanged.
  const page = Math.floor(params.offset / params.limit) + 1;
  const size = params.limit;
  if (Array.isArray(data)) {
    return {
      items: data as BootstrapConversationPage["items"],
      total: data.length,
      page,
      size,
      next_cursor: null,
      has_more: false,
    };
  }
  if (!data || typeof data !== "object") return null;
  const envelope = data as Record<string, unknown>;
  const items = Array.isArray(envelope.items)
    ? (envelope.items as BootstrapConversationPage["items"])
    : [];
  return {
    items,
    total: items.length,
    page,
    size,
    next_cursor: typeof envelope.next_cursor === "string" ? envelope.next_cursor : null,
    has_more: envelope.has_more === true,
  };
}

async function loadConversations(
  path: string,
  params: Required<BootstrapParams>,
  accessToken?: string,
): Promise<SubResult<BootstrapConversationPage>> {
  try {
    // The first call uses proxyJson, exactly like /api/conversations. Only the
    // post-refresh retry needs backendFetch's explicit accessToken escape hatch.
    const res = accessToken
      ? await backendFetch(path, { method: "GET" }, { accessToken })
      : await proxyJson(path);
    if (!res.ok) {
      return {
        value: unavailableConversations(params, res.status),
        status: res.status,
      };
    }
    const normalized = normalizeConversations(await readJson(res), params);
    if (!normalized) {
      return {
        value: unavailableConversations(params, res.status),
        status: res.status,
      };
    }
    return { value: normalized, status: res.status };
  } catch {
    return { value: unavailableConversations(params), status: 0 };
  }
}

async function loadSettings(accessToken?: string): Promise<SubResult<BootstrapSettings>> {
  try {
    const res = accessToken
      ? await backendFetch("/settings", { method: "GET" }, { accessToken })
      : await proxyJson("/settings");
    if (!res.ok) return { value: unavailableSettings(res.status), status: res.status };
    const data = await readJson(res);
    if (!data || typeof data !== "object" || Array.isArray(data)) {
      return { value: unavailableSettings(res.status), status: res.status };
    }
    return { value: data as BootstrapSettings, status: res.status };
  } catch {
    return { value: unavailableSettings(), status: 0 };
  }
}

export async function GET(request: NextRequest) {
  const params = parseParams(request);
  const conversationsPath =
    `/conversations?limit=${params.limit}&offset=${params.offset}&archived=${params.archived}`;

  // All three upstreams are INVOKED in this same tick; none is awaited before
  // the next one starts. Each loader absorbs its own failures, so allSettled
  // only guards against genuinely unexpected rejections.
  const [authSettled, conversationsSettled, settingsSettled] =
    await Promise.allSettled([
      resolveAuth(),
      loadConversations(conversationsPath, params),
      loadSettings(),
    ]);

  const auth = settledValue(authSettled, { result: unavailableAuth(503) });
  let conversations = settledValue(conversationsSettled, {
    value: unavailableConversations(params),
    status: 0,
  });
  let settings = settledValue(settingsSettled, {
    value: unavailableSettings(),
    status: 0,
  });

  // The parallel phase necessarily carries the *old* cookie. If auth healed an
  // expired token, only the 401/403 sub-results are retried with the fresh one —
  // this is the same self-healing the client's nexusFetch performs today, just
  // without a second browser round trip.
  const freshToken = auth.refreshed?.accessToken;
  const retryConversations =
    !!freshToken && (conversations.status === 401 || conversations.status === 403);
  const retrySettings =
    !!freshToken && (settings.status === 401 || settings.status === 403);
  if (freshToken && (retryConversations || retrySettings)) {
    const [retriedConversations, retriedSettings] = await Promise.all([
      retryConversations
        ? loadConversations(conversationsPath, params, freshToken)
        : Promise.resolve(conversations),
      retrySettings ? loadSettings(freshToken) : Promise.resolve(settings),
    ]);
    conversations = retriedConversations;
    settings = retriedSettings;
  }

  const authUnavailable =
    !auth.result.authenticated &&
    "unavailable" in auth.result &&
    auth.result.unavailable === true;
  // Flat, forward-compatible body: a future single backend call can return
  // exactly this object. A rejected/unverifiable session returns only the auth
  // fields — no stale conversation or settings data from another user.
  const body: BootstrapResponse = auth.result.authenticated
    ? {
        authenticated: true,
        user: auth.result.user,
        conversations: conversations.value,
        settings: settings.value,
        ...(auth.refreshed
          ? {
              refreshed: {
                access: true,
                refresh: !!auth.refreshed.refreshToken,
                expires_in: auth.refreshed.expiresIn,
                refresh_expires_in: auth.refreshed.refreshExpiresIn,
              },
            }
          : {}),
      }
    : auth.result;
  // Same cold-start hint as /api/auth/me's 503.
  const response = json(body, authUnavailable ? { "Retry-After": "2" } : {});

  if (auth.refreshed) {
    const cookieOptions = authCookieOptions(request);
    const tokens = auth.refreshed;
    response.cookies.set(TOKEN_COOKIE, tokens.accessToken, {
      ...cookieOptions,
      maxAge: tokens.expiresIn ?? ACCESS_TOKEN_MAX_AGE,
    });
    // Rotation is opt-in on the backend; re-setting an unrotated refresh token
    // with a fresh lifetime would let the cookie outlive the JWT inside it.
    if (tokens.refreshToken) {
      response.cookies.set(REFRESH_COOKIE, tokens.refreshToken, {
        ...cookieOptions,
        maxAge: tokens.refreshExpiresIn ?? REFRESH_TOKEN_MAX_AGE,
      });
    }
  }

  return response;
}
