import { NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON body" }, { status: 400 });
  }
  return proxyJson("/auth/2fa/disable", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
