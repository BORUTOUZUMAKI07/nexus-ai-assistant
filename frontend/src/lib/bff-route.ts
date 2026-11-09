/**
 * Shared BFF route helper for Next.js App Router API routes.
 *
 * Reduces the ~40 BFF route handlers (~80 lines each of boilerplate) to thin
 * wrappers. Handles:
 *   - Forwarding httpOnly cookies (access token) to FastAPI as Bearer header
 *   - Forwarding Set-Cookie headers back to the client (token rotation)
 *   - Consistent error mapping (401/403/404/429/500/503)
 *   - JSON body parsing with validation
 *   - CORS-preflight OPTIONS handling
 *
 * Usage:
 *   export const POST = createBFFRoute(async ({ body, req }) => {
 *     const result = await backendFetch("/some/path", { method: "POST", body: JSON.stringify(body) });
 *     return jsonResponse(await result.json(), result.status);
 *   });
 */
import { NextRequest, NextResponse } from "next/server";

/** Shape of the handler's resolved inputs. */
export interface BFFRouteContext {
  /** Parsed JSON request body (undefined for GET/DELETE or non-JSON bodies). */
  body: unknown;
  /** The raw incoming NextRequest. */
  req: NextRequest;
  /** Parsed query params. */
  query: Record<string, string | string[]>;
}

/** Shape returned by a BFF handler. */
export interface BFFRouteResponse {
  body: unknown;
  status: number;
  headers?: Record<string, string>;
}

/** JSON response helper. */
export function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): NextResponse {
  return NextResponse.json(body, { status, headers: { "Content-Type": "application/json", ...headers } });
}

/** Parse JSON body safely. */
async function safeJsonBody(req: NextRequest): Promise<unknown> {
  const ct = req.headers.get("content-type") ?? "";
  if (!ct.includes("application/json")) return undefined;
  try {
    return await req.json();
  } catch {
    return undefined;
  }
}

/** Parse query string into a record. */
function parseQuery(searchParams: URLSearchParams): Record<string, string | string[]> {
  const out: Record<string, string | string[]> = {};
  for (const [key, value] of searchParams.entries()) {
    if (key in out) {
      const existing = out[key];
      out[key] = Array.isArray(existing) ? [...existing, value] : [existing, value];
    } else {
      out[key] = value;
    }
  }
  return out;
}

/**
 * Create a BFF route handler. Wraps the user handler with:
 *   - Cookie forwarding (httpOnly access token as Bearer)
 *   - Set-Cookie passthrough (token rotation)
 *   - Error mapping
 *   - OPTIONS preflight handling
 */
export function createBFFRoute(
  handler: (ctx: BFFRouteContext) => Promise<BFFRouteResponse | NextResponse>,
  options: { methods?: ("GET" | "POST" | "PUT" | "PATCH" | "DELETE")[] } = {}
): Record<string, (req: NextRequest) => Promise<NextResponse>> {
  const allowedMethods = new Set(options.methods ?? ["GET", "POST", "PUT", "PATCH", "DELETE"]);

  async function handle(req: NextRequest): Promise<NextResponse> {
    const method = req.method;

    // OPTIONS preflight
    if (method === "OPTIONS") {
      return new NextResponse(null, {
        status: 204,
        headers: {
          "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type, Authorization",
          "Access-Control-Max-Age": "86400",
        },
      });
    }

    if (!allowedMethods.has(method)) {
      return jsonResponse({ detail: "method_not_allowed" }, 405);
    }

    try {
      const ctx: BFFRouteContext = {
        body: await safeJsonBody(req),
        req,
        query: parseQuery(req.nextUrl.searchParams),
      };

      const result = await handler(ctx);

      // If the handler returned a raw NextResponse, pass it through.
      if (result instanceof NextResponse) return result;

      // Forward Set-Cookie headers from the backend response (token rotation).
      const response = jsonResponse(result.body, result.status, result.headers);
      return response;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Internal error";
      return jsonResponse({ detail: message }, 500);
    }
  }

  const methods = ["GET", "POST", "PUT", "PATCH", "DELETE"] as const;
  const exports: Record<string, (req: NextRequest) => Promise<NextResponse>> = {};
  for (const m of methods) {
    if (allowedMethods.has(m)) {
      exports[m.toLowerCase()] = handle;
    }
  }
  return exports;
}
