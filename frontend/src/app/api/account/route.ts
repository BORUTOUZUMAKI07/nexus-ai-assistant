import { proxyJson } from "@/lib/proxy";

/**
 * Delete the signed-in user's own account.
 *
 * The backend has exposed `DELETE /api/v1/account` (cascading the user's data)
 * all along, but no BFF route reached it, so the only way to close an account
 * was a direct database operation. Left-over test accounts could not be removed
 * through the product at all.
 */
export async function DELETE() {
  return proxyJson("/account", { method: "DELETE" });
}

/** Data export (portability): the same gap on the read side. */
export async function GET() {
  return proxyJson("/account/export", { method: "GET" });
}
