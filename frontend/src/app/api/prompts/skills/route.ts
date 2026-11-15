import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

/**
 * Active agent skills (the skill rows compiled into the system prompt).
 *
 * Read-only: skills are seeded/enabled server-side, so this route exists to make
 * the set visible in Settings rather than to edit it.
 */
export async function GET(req: NextRequest) {
  const enabledOnly = req.nextUrl?.searchParams.get("enabled_only") ?? "true";
  return proxyJson(`/prompts/skills?enabled_only=${encodeURIComponent(enabledOnly)}`);
}
