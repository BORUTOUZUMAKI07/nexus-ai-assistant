import { NextRequest, NextResponse } from "next/server";
import { backendFetch } from "@/lib/proxy";

export async function POST(req: NextRequest) {
  const form = await req.formData();
  const file = form.get("file");

  if (!(file instanceof Blob)) {
    return NextResponse.json({ detail: "file field is required" }, { status: 400 });
  }

  const backendForm = new FormData();
  backendForm.append("file", file);
  const conversationId = form.get("conversation_id");
  if (conversationId) backendForm.append("conversation_id", String(conversationId));

  const res = await backendFetch("/files/upload", {
    method: "POST",
    body: backendForm,
  });

  const text = await res.text();
  if (!text) return new NextResponse(null, { status: res.status });
  try {
    return NextResponse.json(JSON.parse(text), { status: res.status });
  } catch {
    return new NextResponse(text, { status: res.status });
  }
}