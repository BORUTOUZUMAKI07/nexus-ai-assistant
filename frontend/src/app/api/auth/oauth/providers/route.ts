import { proxyJson } from "@/lib/proxy";

/**
 * GET /api/auth/oauth/providers — which SSO providers this deployment can use.
 *
 * The sign-in page needs this before it has a session, which is why the backend
 * endpoint is unauthenticated and why this relay adds no credential of its own.
 *
 * Sits beside `[provider]/route.ts` in the same segment. The App Router
 * resolves a static segment ahead of a dynamic one, so this handler wins over
 * the sibling catch-all — the same precedence rule FastAPI applies, and the same
 * reason the backend declares its literal route first. If that ever stops
 * holding, this path answers with the "not configured" body for a provider
 * named "providers" and the page silently renders no buttons.
 */
export async function GET() {
  return proxyJson("/auth/oauth/providers", { method: "GET" });
}