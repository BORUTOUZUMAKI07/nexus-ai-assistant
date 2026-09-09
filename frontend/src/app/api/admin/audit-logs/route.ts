import { proxyJson } from "@/lib/proxy";

export async function GET() {
  return proxyJson("/admin/audit-logs");
}