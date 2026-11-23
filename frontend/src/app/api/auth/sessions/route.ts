import { proxyJson } from "@/lib/proxy";

/** Active sign-ins for the current user. The browser never reads a token. */
export async function GET() {
  return proxyJson("/auth/sessions");
}
