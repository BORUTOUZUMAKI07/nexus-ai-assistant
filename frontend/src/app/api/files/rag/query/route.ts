import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

export async function POST(req: NextRequest) {
  const body = await req.json();
  return proxyJson("/files/rag/query", {
    method: "POST",
    body: JSON.stringify(body),
  });
}