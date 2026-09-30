/**
 * Upload size guards for the BFF.
 *
 * Why the BFF has to enforce this even though the backend already does
 * ─────────────────────────────────────────────────────────────────
 * `api/v1/files.py` enforces `MAX_UPLOAD_SIZE_MB` carefully: it checks
 * `file.size` first, then reads the body in 1 MB chunks and aborts the moment
 * the running total passes the ceiling, so FastAPI never holds a large upload
 * in memory.
 *
 * All of that work is undone one hop earlier. `req.formData()` in a route
 * handler materialises the entire multipart body into the Node process's heap
 * before a single byte is forwarded. So a single multi-gigabyte request does not
 * reach the backend's streaming guard at all — it exhausts the BFF first, and
 * `req.formData()` rejects with an opaque error that surfaces as a 500. The
 * careful code downstream protects a resource that has already been spent.
 *
 * Checking `Content-Length` before calling `formData()` moves the rejection
 * ahead of the allocation.
 *
 * What this is not
 * ────────────────
 * A `Content-Length` pre-check is not a hard limit, because the header is
 * supplied by the client and can be absent (chunked transfer) or understated.
 * It is a cheap, effective gate for ordinary clients — every browser sets it for
 * `FormData` — and it converts the common case from "OOM the server" into a
 * clean 413. Turning this into an unbypassable ceiling needs a streaming body
 * reader that aborts mid-transfer, which is a larger change than this guard;
 * the honest place for the remaining half of that defence is a reverse proxy
 * with `client_max_body_size`, which cannot be lied to.
 */

const MB = 1024 * 1024;

/**
 * Mirrors `MAX_UPLOAD_SIZE_MB` in `backend/app/core/config.py` (default 50).
 *
 * Deliberately read from the server environment rather than a `NEXT_PUBLIC_`
 * variable: it is a proxy-side resource limit, and it must be overridable in
 * deployment without becoming a client-visible value.
 *
 * The two must stay in agreement. The backend is the authority on what it will
 * accept; this exists so the BFF does not have to buffer a request the backend
 * was always going to reject. Setting this lower than the backend rejects
 * uploads the backend would have taken; setting it higher only wastes memory
 * before the backend's own guard fires.
 */
export const MAX_UPLOAD_BYTES =
  Number(process.env.MAX_UPLOAD_SIZE_MB ?? 50) * MB;

/**
 * Ceiling for the transcription upload.
 *
 * The backend has no limit here at all — `api/v1/audio.py` does a bare
 * `await file.read()` — so this number is chosen rather than mirrored. The
 * input is a browser `MediaRecorder` blob of a spoken message; 25 MB is roughly
 * half an hour of Opus and far beyond any real voice note, while still being
 * small enough that the BFF can hold one without pressure.
 */
export const MAX_AUDIO_UPLOAD_BYTES = 25 * MB;

/**
 * Rejects an over-sized body before it is read, or `null` if it may proceed.
 *
 * Returns a `Response` rather than throwing so a route can `return` it directly
 * and stay on its existing single-return shape.
 */
export function rejectOversizedBody(
  contentLength: string | null,
  maxBytes: number,
  what: string,
): Response | null {
  // Absent or unparseable means "unknown", and unknown is allowed through: a
  // chunked request carries no length by design, and rejecting it would break
  // legitimate clients for a limit we cannot even evaluate. The reverse-proxy
  // ceiling above is what bounds those.
  if (contentLength === null) return null;

  const declared = Number(contentLength);
  if (!Number.isFinite(declared) || declared < 0) return null;
  if (declared <= maxBytes) return null;

  const limitMb = Math.round(maxBytes / MB);
  return new Response(
    JSON.stringify({
      detail: `${what} exceeds the ${limitMb}MB limit.`,
      max_bytes: maxBytes,
    }),
    {
      status: 413,
      headers: {
        "Content-Type": "application/json",
        // A 413 is not retryable without a smaller body, so explicitly decline
        // the default "retry soon" reading of a 5xx-family code.
        "Cache-Control": "no-store",
      },
    },
  );
}
