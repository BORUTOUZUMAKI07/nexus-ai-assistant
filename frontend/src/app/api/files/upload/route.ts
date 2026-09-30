import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";
import { MAX_UPLOAD_BYTES, rejectOversizedBody } from "@/lib/upload-limits";

export async function POST(req: NextRequest) {
  // Checked before `formData()`. That call buffers the whole multipart body into
  // this process's heap, so the size has to be judged before it happens — see
  // the rationale in lib/upload-limits.ts. The backend enforces the same limit
  // while streaming; this is the check that runs before the memory is spent.
  const tooLarge = rejectOversizedBody(
    req.headers.get("content-length"),
    MAX_UPLOAD_BYTES,
    "File",
  );
  if (tooLarge) return tooLarge;

  const form = await req.formData();
  return proxyJson("/files/upload", { method: "POST", body: form });
}