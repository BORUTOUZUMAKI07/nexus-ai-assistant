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
  // Any non-2xx from the backend means this instance cannot serve requests, so
  // an upstream 500/502 must not be reported as "ready" -- that hid a real
  // outage behind a green health check. `backend.status >= 400` keeps the
  // same no-restart-storm behaviour while telling the truth.
  const ready = backend.status >= 200 && backend.status < 400;
  return Response.json(
    { status: ready ? "ready" : "degraded", timestamp: Date.now() },
    { status: ready ? 200 : 503, headers: { "Cache-Control": "no-store" } }
  );
}
