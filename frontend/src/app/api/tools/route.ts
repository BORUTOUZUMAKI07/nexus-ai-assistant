import { proxyJson } from "@/lib/proxy";

/**
 * The tools the agent can call (including FastMCP tools).
 *
 * Same story as the prompt library: the backend endpoint existed with no BFF
 * route, so nothing in the product could show which capabilities the agent
 * actually has.
 */
export async function GET() {
  return proxyJson("/tools");
}
