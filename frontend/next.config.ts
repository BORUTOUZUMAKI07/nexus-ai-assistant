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

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ["localhost", "127.0.0.1"],
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(self), geolocation=()" },
          { key: "Content-Security-Policy", value: csp },
        ],
      },
    ];
  },
};

export default nextConfig;