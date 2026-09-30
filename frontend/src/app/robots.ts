import type { MetadataRoute } from "next";

/**
 * Crawl policy.
 *
 * The root layout also sets `robots: { index: false }`, which covers every page
 * individually. This file is the belt-and-braces version: it also stops a
 * crawler from *discovering* the private routes in the first place, which the
 * per-page meta tag cannot do. A crawler that never receives a link to `/app`
 * never requests it, never renders the session gate, and never gets logged as a
 * failed auth attempt in the backend's audit log.
 *
 * The explicit `Disallow` entries matter for the opposite reason too: `/api` is
 * the BFF. Every handler there is cookie-authenticated, but a crawler fetching
 * one is an unauthenticated request that the backend will 401 — noise in the
 * metrics, and a route with side effects on a mutating verb is exactly what a
 * crawler should never be poking.
 */
export default function robots(): MetadataRoute.Robots {
  const siteUrl =
    process.env.NEXT_PUBLIC_SITE_URL ??
    (process.env.VERCEL_URL ? `https://${process.env.VERCEL_URL}` : "http://localhost:3000");

  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: ["/app", "/app/", "/api/", "/signin"],
      },
    ],
    sitemap: `${siteUrl}/sitemap.xml`,
  };
}
