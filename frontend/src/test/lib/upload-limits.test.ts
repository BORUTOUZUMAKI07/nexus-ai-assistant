import { describe, expect, it } from "vitest";
import {
  MAX_AUDIO_UPLOAD_BYTES,
  MAX_UPLOAD_BYTES,
  rejectOversizedBody,
} from "@/lib/upload-limits";

/**
 * The 413 guard has to reject *before* `req.formData()` runs, which means it is
 * the only thing standing between an unauthenticated-looking large POST and an
 * out-of-memory kill of the BFF process. If it regresses to checking after the
 * body is read it is worse than useless, so these assert both the verdict and
 * the specific reason each verdict is allowed.
 */
describe("rejectOversizedBody", () => {
  const MB = 1024 * 1024;

  it("lets a body under the ceiling through", () => {
    expect(rejectOversizedBody(String(1 * MB), MAX_UPLOAD_BYTES, "File")).toBeNull();
  });

  it("rejects a body over the ceiling with a 413", async () => {
    const res = rejectOversizedBody(String(500 * MB), MAX_UPLOAD_BYTES, "File");
    expect(res).not.toBeNull();
    expect(res!.status).toBe(413);
    const body = await res!.json();
    expect(body.detail).toContain("50MB");
    expect(body.max_bytes).toBe(MAX_UPLOAD_BYTES);
  });

  it("accepts a body exactly at the ceiling", () => {
    // Off-by-one guard. `>` and `>=` must agree on the boundary, and a client
    // told "the limit is 50MB" must be able to actually send 50MB.
    expect(rejectOversizedBody(String(MAX_UPLOAD_BYTES), MAX_UPLOAD_BYTES, "File")).toBeNull();
  });

  it("allows an absent Content-Length rather than guessing", () => {
    // Chunked transfer encoding carries no length by design. Rejecting it would
    // break legitimate clients over a limit we cannot even evaluate.
    expect(rejectOversizedBody(null, MAX_UPLOAD_BYTES, "File")).toBeNull();
  });

  it("treats an unparseable or negative length as unknown", () => {
    expect(rejectOversizedBody("not-a-number", MAX_UPLOAD_BYTES, "File")).toBeNull();
    expect(rejectOversizedBody("-1", MAX_UPLOAD_BYTES, "File")).toBeNull();
    expect(rejectOversizedBody("", MAX_UPLOAD_BYTES, "File")).toBeNull();
  });

  it("names the offending resource in the error", () => {
    const res = rejectOversizedBody(String(99 * MB), MAX_AUDIO_UPLOAD_BYTES, "Audio");
    expect(res!.status).toBe(413);
    return res!.json().then((body: { detail: string }) => {
      expect(body.detail).toMatch(/^Audio exceeds the 25MB limit\.$/);
    });
  });

  it("marks the rejection uncacheable", () => {
    // A 413 must not be stored by an intermediary, or a later upload of the
    // same URL would replay the rejection.
    const res = rejectOversizedBody(String(500 * MB), MAX_UPLOAD_BYTES, "File");
    expect(res!.headers.get("Cache-Control")).toBe("no-store");
  });
});

describe("upload ceilings", () => {
  it("keeps the BFF file limit aligned with the backend default", () => {
    // MAX_UPLOAD_SIZE_MB in backend/app/core/config.py defaults to 50. If the two
    // drift, the BFF either rejects uploads the backend would have accepted or
    // buffers requests the backend was always going to refuse.
    expect(MAX_UPLOAD_BYTES).toBe(50 * 1024 * 1024);
  });

  it("uses a distinct, smaller ceiling for audio", () => {
    // The backend has no ceiling on the audio path at all, so the BFF value is
    // chosen, not mirrored — and must not accidentally inherit the file limit.
    expect(MAX_AUDIO_UPLOAD_BYTES).toBe(25 * 1024 * 1024);
    expect(MAX_AUDIO_UPLOAD_BYTES).toBeLessThan(MAX_UPLOAD_BYTES);
  });
});
