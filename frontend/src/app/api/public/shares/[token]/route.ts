import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ token: string }> };

/**
 * Public, read-only conversation content behind a share token.
 *
 * Deliberately unauthenticated: the recipient of a share link has no session.
 * `proxyJson` still attaches the cookie when one exists, which is harmless --
 * the backend endpoint ignores credentials and serves content + owners only.
 */
export async function GET(_req: NextRequest, { params }: Ctx) {
  const { token } = await params;
  return proxyJson(`/public/shares/${encodeURIComponent(token)}`);
}