import { proxyJson } from "@/lib/proxy";

/** Start TOTP enrollment. The secret stays server-side until the UI displays it. */
export async function POST() {
  return proxyJson("/auth/2fa/setup", { method: "POST" });
}
