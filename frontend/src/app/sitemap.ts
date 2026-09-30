import type { MetadataRoute } from "next";

/**
 * Sitemap for the two public routes.
 *
 * Kept deliberately short. `/app` is an authenticated shell whose contents are
 * per-user, so no per-conversation or per-document URL belongs here — listing
 * them would advertise the shape of the app to anyone who fetches one XML file,
 * and would require a database read at build time to do it. The landing page is
 * the only URL with content worth ranking.
 *
 * `/signin` is intentionally absent. It carries `noindex` via the root layout,
 * and putting a `noindex` URL in a sitemap is a contradiction crawlers resolve by
 * ignoring the sitemap's own directives.
 */
export default function sitemap(): MetadataRoute.Sitemap {
  const siteUrl =
    process.env.NEXT_PUBLIC_SITE_URL ??
    (process.env.VERCEL_URL ? `https://${process.env.VERCEL_URL}` : "http://localhost:3000");

  return [
    {
      url: siteUrl,
      // No `lastModified`: nothing here is content-managed, so a timestamp would
      // be a lie that only makes crawlers re-fetch more often.
      changeFrequency: "monthly",
      priority: 1,
    },
  ];
}
