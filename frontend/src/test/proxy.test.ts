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

function request(
  pathname: string,
  cookieValue?: string,
  refreshCookie?: string,
): NextRequest {
  const req = new NextRequest(`${ORIGIN}${pathname}`);
  if (cookieValue !== undefined) {
    req.cookies.set("nexus_access_token", cookieValue);
  }
  if (refreshCookie !== undefined) {
    req.cookies.set("nexus_refresh_token", refreshCookie);
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

  it("lets a tab through on a refresh cookie alone", () => {
    // Measured defect: the access token lives one hour, the refresh token thirty
    // days. Gating on the access cookie alone meant a tab was redirected to
    // /signin an hour into a perfectly valid session, and the probe that would
    // have healed it never ran. This is the case that fix depends on.
    const res = proxy(request("/app", undefined, "r.e.f"));
    expect(res.status).toBe(200);
    expect(res.headers.get("location")).toBeNull();
  });

  it("still redirects when the only cookie has an empty value", () => {
    const res = proxy(request("/app", "", ""));
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

  it("scopes the matcher to the workspace and the sign-in page", () => {
    // `/` must stay anonymous-reachable and `/api/*` belongs to the route
    // handlers. A matcher widened to `/:path*` would make the public landing
    // page require a session, which is the single most damaging way this file
    // could be misconfigured. `/signin` is included deliberately: it is the only
    // way a visitor who is already signed in can be sent away from the form.
    expect(config.matcher).toEqual(["/app", "/app/:path*", "/signin"]);
  });

  it("sends a signed-in visitor away from the sign-in page", () => {
    // With a live session, the form is a trap: pressing "Continue with
    // Google/GitHub" starts a second consent round trip on top of a working one,
    // and a provider mismatch lands the user on an error page for a session that
    // was never broken. The url-shortner project redirects /login to the app for
    // exactly this reason; this is the same rule.
    const res = proxy(request("/signin", "a.b.c"));
    expect(res.status).toBe(307);
    expect(new URL(res.headers.get("location")!).pathname).toBe("/app");
  });

  it("treats a refresh cookie alone as a session on the sign-in page too", () => {
    // The same rule as /app: plenty of valid sessions have an expired access
    // cookie and a live refresh one, and showing them the form would push them
    // into re-authenticating on top of a session that still works.
    const res = proxy(request("/signin", undefined, "r.e.f"));
    expect(res.status).toBe(307);
    expect(new URL(res.headers.get("location")!).pathname).toBe("/app");
  });

  it("still lets a failed attempt explain itself on the sign-in page", () => {
    // The OAuth callback and the TOTP bounce both land back on /signin with a
    // reason attached. Bouncing those to /app would swallow the only message the
    // user gets about a sign-in that did not work.
    for (const purpose of ["?error=oauth_callback_failed_401", "?oauth=2fa", "?code=abc"]) {
      const res = proxy(request(`/signin${purpose}`, "a.b.c"));
      expect(res.status, `bounced a result of an attempt: ${purpose}`).toBe(200);
      expect(res.headers.get("location")).toBeNull();
    }
  });

  it("never redirects the anonymous sign-in page to itself", () => {
    // /signin is where anonymous visitors are sent, so the unauthenticated
    // branch has to exempt it — otherwise matching it would produce a redirect
    // to the same URL, i.e. an infinite loop on the one page everyone sees
    // before signing in.
    const res = proxy(request("/signin"));
    expect(res.status).toBe(200);
    expect(res.headers.get("location")).toBeNull();
  });

  it("keeps the refresh cookie name in sync with the auth module", async () => {
    const { REFRESH_COOKIE } = await import("@/lib/auth");
    expect(REFRESH_COOKIE).toBe("nexus_refresh_token");
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
