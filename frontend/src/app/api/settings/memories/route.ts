import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

export async function GET() {
  return proxyJson("/settings/memories");
}

export async function POST(req: NextRequest) {
  const body = await req.json();
  return proxyJson("/settings/memories", {
    method: "POST",
    body: JSON.stringify(body),
  });
}