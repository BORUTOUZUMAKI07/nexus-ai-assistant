import { backendFetch } from "@/lib/proxy";

/**
 * Health check endpoint for the hosting platform.
 *
 * Liveness: returns 200 when the Next.js process itself is up.
 * Readiness: also verifies the BFF can reach the backend (FastAPI) by probing
 * `/api/v1/auth/me` — that endpoint requires no session cookie and answers
 * immediately, so a successful response means the backend is alive.
 *
 * Platforms (Vercel, Kubernetes, Docker) ping this to decide whether to route
 * traffic or restart an instance. Keeps dependencies out of the liveness path
 * so a downstream outage never triggers a restart storm.
 */
export async function GET() {
  const backend = await backendFetch("/health", {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });
  const ready = backend.status !== 503;
  return Response.json(
    { status: ready ? "ready" : "degraded", timestamp: Date.now() },
    { status: ready ? 200 : 503, headers: { "Cache-Control": "no-store" } }
  );
}
