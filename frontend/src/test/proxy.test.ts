import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { proxy, config } from "@/proxy";

/**
 * The edge session gate had no test coverage at all.
 *
 * That is the worst place in this app for a coverage hole: `src/proxy.ts` is the
 * one file that decides whether an anonymous visitor receives the workspace HTML
 * and JS bundle, it runs on every navigation to `/app`, and it is the only piece
 * of the auth story that executes *before* any client code. `src/test/lib/proxy.test.ts`
 * covers the other proxy (`lib/proxy.ts`, the BFF helper) — the two are unrelated
 * files that happen to share a name, which is exactly how one of them ends up
 * looking covered when it is not.
 */
const ORIGIN = "https://nexus.example";

function request(pathname: string, cookieValue?: string): NextRequest {
  const req = new NextRequest(`${ORIGIN}${pathname}`);
  if (cookieValue !== undefined) {
    req.cookies.set("nexus_access_token", cookieValue);
  }
  return req;
}

describe("edge session gate", () => {
  it("redirects an anonymous visitor to /signin", () => {
    const res = proxy(request("/app"));
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toContain("/signin");
  });

  it("lets a request carrying the cookie through", () => {
    const res = proxy(request("/app", "a.b.c"));
    // `NextResponse.next()` is the identity passthrough: no redirect status and
    // no `x-middleware-next` header means the request proceeds.
    expect(res.status).toBe(200);
    expect(res.headers.get("location")).toBeNull();
  });

  it("treats an empty cookie value as no session", () => {
    // A present-but-empty cookie is what a cleared session can leave behind, and
    // `?.value` is a falsy check precisely so this case redirects. Worth pinning:
    // switching to a `!== undefined` test would let it through.
    const res = proxy(request("/app", ""));
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toContain("/signin");
  });

  it("preserves the intended destination for a return after sign-in", () => {
    const res = proxy(request("/app/knowledge"));
    const location = new URL(res.headers.get("location")!);
    expect(location.pathname).toBe("/signin");
    expect(location.searchParams.get("next")).toBe("/app/knowledge");
  });

  it("never emits a protocol-relative or absolute `next`", () => {
    // The open-redirect guard. `?next=` is consumed by /signin, so a value that
    // can be made absolute would send a freshly authenticated user to an
    // attacker's site right after signing in — the most valuable moment in the
    // flow to be phished at.
    //
    // Each hostile form is fed through a real URL, so it arrives as the actual
    // `pathname` the gate reads. Asserting on the parsed value rather than on a
    // helper's return keeps this honest about what the gate really emits.
    const hostile = [
      "//evil.example/steal", // protocol-relative: starts with "/" but is absolute
      "/app/..//evil.example", // same, via a dot segment
      "/\\evil.example", // browsers normalise backslash to slash
      "/https://evil.example", // scheme smuggled after a leading slash
      "/javascript:alert(1)", // scheme-like prefix after a leading slash
    ];

    for (const path of hostile) {
      const res = proxy(request(path));
      const location = new URL(res.headers.get("location")!);
      const next = location.searchParams.get("next") ?? "";

      // A dropped value is the correct outcome. Anything forwarded verbatim is a
      // bug, and so is anything that still resolves to a foreign host.
      expect(next, `hostile pathname was forwarded: ${path}`).not.toBe(path);
      expect(next, `hostile pathname resolved off-origin: ${path}`).not.toMatch(
        /^(?:[a-z][a-z0-9+.-]*:|\/\/)/i,
      );
    }
  });

  it("forwards an ordinary workspace path unchanged", () => {
    // The guard must not be so eager that it drops legitimate deep links, which
    // would silently break the post-sign-in return it exists to support.
    const res = proxy(request("/app/settings"));
    const location = new URL(res.headers.get("location")!);
    expect(location.searchParams.get("next")).toBe("/app/settings");
  });

  it("scopes the matcher to the workspace only", () => {
    // `/` and `/signin` must stay anonymous-reachable and `/api/*` belongs to the
    // route handlers. A matcher widened to `/:path*` would make the public
    // landing page require a session, which is the single most damaging way this
    // file could be misconfigured.
    expect(config.matcher).toEqual(["/app", "/app/:path*"]);
  });

  it("keeps the cookie name in sync with the auth module", async () => {
    // The gate hardcodes "nexus_access_token" because the edge runtime cannot
    // import lib/auth.ts (it pulls in next/headers). That duplication is the
    // load-bearing risk here: renaming the cookie without updating this would
    // lock every signed-in user out of /app with no error anywhere.
    const { TOKEN_COOKIE } = await import("@/lib/auth");
    expect(TOKEN_COOKIE).toBe("nexus_access_token");
  });
});
