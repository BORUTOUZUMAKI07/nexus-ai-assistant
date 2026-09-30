import type { NextConfig } from "next";

// Content-Security-Policy.
//
// Strengthened with the directives that were missing: frame-ancestors,
// object-src, base-uri, form-action, font-src and upgrade-insecure-requests.
//
// `frame-ancestors 'none'` is the substantive addition. The app previously sent
// only `X-Frame-Options: DENY`, which covers clickjacking in browsers that
// honour it, but CSP's frame-ancestors is the modern, broader control and takes
// precedence where both are present. `object-src 'none'` and `base-uri 'self'`
// close off plugin and <base>-injection vectors that the old policy left to
// `default-src 'self'`, which permits same-origin plugin content.
//
// `script-src` keeps `'unsafe-inline'`. Removing it requires a per-request
// nonce, which the Next.js docs are explicit forces every page to be
// dynamically rendered — no static optimisation, no ISR, no CDN caching, and
// incompatible with Partial Prerendering. That is a real availability and
// latency cost, not a free security win, so it is not taken unilaterally here.
// The nonce path is a deliberate, opt-in change: move this header into
// `src/proxy.ts` (see the Content-Security-Policy guide) if strict CSP is a
// stated requirement. See the scope note in src/proxy.ts.
const isDev = process.env.NODE_ENV === "development";

const csp = [
  "default-src 'self'",
  // React uses eval in development to reconstruct server-side stack traces.
  "script-src 'self' 'unsafe-inline'" + (isDev ? " 'unsafe-eval'" : ""),
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "upgrade-insecure-requests",
].join("; ");

/**
 * HSTS, sent on every response including the ones a browser would reach over
 * plain HTTP. That is intentional and safe: a browser only ever honours this
 * header when it arrived over HTTPS, so serving it to an `http://<lan-ip>:3000`
 * dev request is a no-op rather than a lockout.
 *
 * `preload` is deliberately omitted. It is not a header flag the app can decide
 * unilaterally -- submitting a domain to the preload list makes browsers pin it
 * to HTTPS for a year with a slow, manual removal path, and that belongs to the
 * operator who controls the DNS, not to a default in a build config. `max-age`
 * still gives the full protection the app itself is responsible for; add
 * `preload` here once the deployment is settled and the domain is owned.
 */
const hsts = "max-age=63072000; includeSubDomains";

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ["localhost", "127.0.0.1"],

  // React Compiler. Auto-memoization is what this app was missing: the chat shell
  // re-renders on every streamed token, and hand-written memoization was both
  // incomplete (23 useCallbacks, 1 useMemo across 110 files) and about to become
  // actively redundant. Next 16 promotes this from `experimental` to stable, and
  // ships an SWC pass that only compiles files containing JSX or hooks, so the
  // build cost is localized rather than a full Babel pass over the tree.
  reactCompiler: true,

  // Do not advertise the framework and its exact version to anyone who asks.
  // `X-Powered-By: Next.js` is a free version-fingerprint for a scanner, and it
  // buys nothing for the client.
  poweredByHeader: false,

  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(self), geolocation=()" },
          { key: "Strict-Transport-Security", value: hsts },
          { key: "Content-Security-Policy", value: csp },
        ],
      },
    ];
  },
};

export default nextConfig;