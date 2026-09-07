import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

export async function GET() {
  return proxyJson("/settings");
}

export async function PUT(req: NextRequest) {
  const body = await req.json();
  return proxyJson("/settings", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}