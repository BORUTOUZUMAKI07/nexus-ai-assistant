import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

export async function GET(req: NextRequest) {
  const search = req.nextUrl.search;
  return proxyJson(`/files${search}`);
}