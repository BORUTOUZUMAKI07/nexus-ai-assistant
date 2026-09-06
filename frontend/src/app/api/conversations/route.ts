import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

export async function GET(req: NextRequest) {
  const search = req.nextUrl.search;
  return proxyJson(`/conversations${search}`);
}

export async function POST(req: NextRequest) {
  const body = await req.json();
  return proxyJson("/conversations", {
    method: "POST",
    body: JSON.stringify(body),
  });
}