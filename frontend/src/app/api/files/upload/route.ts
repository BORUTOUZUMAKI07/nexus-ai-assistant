import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

export async function POST(req: NextRequest) {
  const form = await req.formData();
  return proxyJson("/files/upload", { method: "POST", body: form });
}