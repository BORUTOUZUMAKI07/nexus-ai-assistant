/**
 * Client-side cache guidance for the additive bootstrap BFF endpoint.
 *
 * Another owner can wire the shell to one round trip by replacing the three
 * mount fetches with:
 *
 *   const bootstrap = useQuery(bootstrapQueryOptions());
 *
 * and seeding `auth` / `conversations.items` / `settings` into the existing
 * query cache from `bootstrap.data`. This module deliberately does NOT edit
 * `page.tsx`; it only provides the query option and its fetcher.
 *
 * The stale time is intentionally short but non-zero: the shell's first paint
 * must not re-fetch on every render or on window focus, yet auth and backend
 * liveness can change within a session, so a long-lived cache would resurrect
 * the very staleness this endpoint exists to remove.
 *
 * WIRING NOTE for the page.tsx owner: use plain `fetch` (as here), NOT
 * `nexusFetch`. The BFF already owns the 401/refresh leg; a client auto-refresh
 * would duplicate it. A 503 must be retried with the shell's existing backoff,
 * because `authenticated: false` without `unavailable: true` is the only signed
 * -out verdict; the aggregate route itself always answers 200.
 */
import { queryOptions } from "@tanstack/react-query";
import {
  DEFAULT_BOOTSTRAP_PARAMS,
  type BootstrapParams,
  type BootstrapResponse,
} from "./bootstrap-types";

/** How long a bootstrap result is considered fresh (milliseconds). */
export const BOOTSTRAP_STALE_TIME_MS = 30_000;

export const bootstrapKeys = {
  all: ["bootstrap"] as const,
};

/** Build the exact query string the route expects. */
export function bootstrapSearch(params: BootstrapParams = {}): string {
  const resolved = { ...DEFAULT_BOOTSTRAP_PARAMS, ...params };
  const search = new URLSearchParams({
    limit: String(resolved.limit),
    offset: String(resolved.offset),
    archived: String(resolved.archived),
  });
  return search.toString();
}

/** Fetch the bootstrap object. Never cache it in the browser HTTP cache. */
export async function fetchBootstrap(
  params: BootstrapParams = {},
): Promise<BootstrapResponse> {
  const res = await fetch(`/api/bootstrap?${bootstrapSearch(params)}`, {
    method: "GET",
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Bootstrap failed: ${res.status}`);
  return (await res.json()) as BootstrapResponse;
}

/**
 * Query option for `useQuery(bootstrapQueryOptions())`.
 *
 * `staleTime` prevents a render-driven refetch storm; `refetchOnWindowFocus`
 * is off because the shell already invalidates specific caches on mutation.
 */
export function bootstrapQueryOptions(params: BootstrapParams = {}) {
  return queryOptions({
    queryKey: [...bootstrapKeys.all, { ...DEFAULT_BOOTSTRAP_PARAMS, ...params }] as const,
    queryFn: () => fetchBootstrap(params),
    staleTime: BOOTSTRAP_STALE_TIME_MS,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}
