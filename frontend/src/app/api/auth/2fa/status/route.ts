import { proxyJson } from "@/lib/proxy";

/** Whether TOTP is enabled for the signed-in user. */
export async function GET() {
  return proxyJson("/auth/2fa/status");
}
