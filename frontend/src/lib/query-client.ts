import { QueryClient } from "@tanstack/react-query";

/**
 * Production query client for the app shell.
 *
 * The chat stream is deliberately *not* on this client. `useNexusChat` owns the
 * transcript and consumes SSE directly; everything else (the four lazy views,
 * the conversation/artifact/plan lists) is server state that benefits from the
 * cache, dedupe and lifecycle this client provides.
 *
 * - retry: 1 — one silent retry absorbs a transient network blip without
 *   turning a slow backend into three sequential failures for the user.
 * - staleTime: 30s — a list revalidates in the background once it is older than
 *   half a minute; a fresh tab switch renders instantly from cache.
 * - refetchOnWindowFocus: false — this is a chat application; losing focus to a
 *   code editor and coming back is not a signal to refetch every open query.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: 1,
        staleTime: 30_000,
        refetchOnWindowFocus: false,
      },
    },
  });
}

/**
 * Test query client.
 *
 * retry: false on both queries and mutations keeps MSW-driven tests
 * deterministic: a handler that returns a 500 must surface that error once,
 * rather than being retried into a delayed unhandled rejection.
 */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}
