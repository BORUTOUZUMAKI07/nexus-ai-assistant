import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const query = searchParams.toString();
  return proxyJson(`/conversations${query ? `?${query}` : ""}`);
}

export async function POST(req: NextRequest) {
  const body = await req.json();
  return proxyJson("/conversations", {
    method: "POST",
    body: JSON.stringify(body),
  });
}