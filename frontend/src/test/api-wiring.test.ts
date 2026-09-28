/**
 * Wiring guard: every /api path the *browser* calls must have a matching
 * Next.js route handler on disk.
 *
 * Why this exists
 * ───────────────
 * The admin console's Monitoring, Optimization and Compliance tabs were broken
 * in production while the whole suite stayed green. `AdminView` called
 * `/api/admin/monitoring/slices`, FastAPI served
 * `/api/v1/admin/monitoring/slices`, and the relay in between —
 * `src/app/api/admin/monitoring/slices/route.ts` — did not exist. Requests
 * 404'd at runtime and nothing failed.
 *
 * Unit tests cannot see this. MSW intercepts fetch at the network layer, and
 * under jsdom the Next.js route handlers never execute, so a mocked URL is
 * indistinguishable from a served one. Deleting the mocks would not help
 * either: there is no server running in a unit test to 404 against.
 *
 * So this closes the gap structurally. It reads ground truth from both sides —
 * the route handlers that exist on disk, and the paths the client actually
 * requests — and compares them. A missing relay fails here even though every
 * other test is green.
 */
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SRC_DIR = resolve(__dirname, "..");
const API_DIR = join(SRC_DIR, "app", "api");

function walkFiles(dir: string, exts: string[], out: string[] = []): string[] {
  if (!existsSync(dir)) return out;
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walkFiles(full, exts, out);
    else if (exts.some((e) => entry.endsWith(e))) out.push(full);
  }
  return out;
}

/**
 * Directories that contain a `route.ts`, expressed as the public path they
 * mount at. `app/api/auth/login/route.ts` serves `/api/auth/login`, so the
 * `/api` prefix is re-attached to the path relative to the api directory.
 */
const handlerDirs = walkFiles(API_DIR, [".ts"])
  .filter((f) => f.endsWith("route.ts"))
  .map((f) => join(f, "..")) // strip the trailing "route.ts"
  .map((d) => "/api/" + relative(API_DIR, d).replace(/\\/g, "/"));

/**
 * Match a request path against the mounted handlers.
 *
 * Both a `[id]` folder name and a `${id}` call-site interpolation count as a
 * dynamic segment, exactly as the App Router resolves them. A dynamic segment
 * matches one path part and nothing else — `/api/artifacts` is not a
 * substitute for `/api/artifacts/[id]`.
 */
function isMounted(requestPath: string): boolean {
  const requestParts = requestPath.split("/").filter(Boolean);
  return handlerDirs.some((handler) => {
    const handlerParts = handler.split("/").filter(Boolean);
    if (handlerParts.length !== requestParts.length) return false;
    return handlerParts.every((part, i) => {
      if (part.startsWith("[") && part.endsWith("]")) return true;
      if (part === "*") return true;
      return part === requestParts[i];
    });
  });
}

/**
 * Turn a fetch call's first argument into a comparable request path.
 *
 * Handles the two forms in use: a literal `"/api/..."` and a template built
 * from `${API_BASE}`. Server-side relays build `${BACKEND_URL}/api/v1/...`
 * instead — those are the handlers doing their job, not browser calls, so they
 * are skipped.
 */
function toRequestPath(firstArg: string): string | null {
  const arg = firstArg.replace(/^`|`$/g, "").replace(/^"|"$/g, "");

  if (arg.includes("BACKEND_URL")) return null;

  const path = arg.includes("${API_BASE}")
    ? arg.replace("${API_BASE}", "/api")
    : arg.startsWith("/api")
      ? arg
      : null;
  if (path === null) return null;

  // Drop any query string: "?limit=10" is not part of the route.
  const withoutQuery = path.split("?")[0];

  const segments = withoutQuery
    .split("/")
    .filter(Boolean)
    .map((segment) => {
      if (!segment.includes("${")) return segment;
      // Fully interpolated segment -> dynamic. A segment like
      // `artifacts${query}` keeps its literal prefix, since that is the real
      // route with a query string tacked on.
      return segment.startsWith("${") ? "*" : segment.split("${")[0];
    })
    // A prefix-only segment ("artifacts" out of "artifacts${query}") can leave
    // an empty tail when the literal part was the whole segment.
    .filter((segment) => segment.length > 0);

  return `/${segments.join("/")}`;
}

// A real call, with the path as its first argument. Matching the call — not
// the path in isolation — is what keeps doc comments and error strings out.
const CALL = /\b(?:nexusFetch|fetchWithRetry|fetch)\s*\(\s*(`[^`]*`|"[^"]*")/g;

describe("API wiring", () => {
  it("finds the route handlers on disk", () => {
    // A guard that silently matches nothing would pass forever.
    expect(handlerDirs.length).toBeGreaterThan(20);
  });

  it("every /api path the browser calls has a route handler", () => {
    const sourceFiles = walkFiles(SRC_DIR, [".ts", ".tsx"]).filter(
      (f) =>
        // The test tree asserts on mock URLs, not on real wiring.
        !f.includes(`${"test"}`) &&
        // Relays call the FastAPI backend, not themselves.
        !f.startsWith(API_DIR)
    );

    const missing = new Set<string>();

    for (const file of sourceFiles) {
      const src = readFileSync(file, "utf8");
      CALL.lastIndex = 0;
      let match: RegExpExecArray | null;
      while ((match = CALL.exec(src)) !== null) {
        const requestPath = toRequestPath(match[1]);
        if (requestPath && !isMounted(requestPath)) {
          const line = src.slice(0, match.index).split("\n").length;
          missing.add(
            `${requestPath}  ←  ${relative(SRC_DIR, file).replace(/\\/g, "/")}:${line}`
          );
        }
      }
    }

    expect(
      [...missing].sort(),
      `These client calls have no Next.js route handler, so they 404 in ` +
        `production. Create the matching file under src/app/api/ — it is a few ` +
        `lines calling proxyJson().\n\n  ${[...missing].sort().join("\n  ")}`
    ).toEqual([]);
  });
});
