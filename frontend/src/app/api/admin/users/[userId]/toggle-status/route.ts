import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ userId: string }> };

export async function POST(_req: NextRequest, { params }: Ctx) {
  const { userId } = await params;
  return proxyJson(`/admin/users/${userId}/toggle-status`, { method: "POST" });
}