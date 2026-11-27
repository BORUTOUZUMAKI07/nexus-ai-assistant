/**
 * Server-side correlation IDs for BFF -> backend calls.
 *
 * A valid ID from the browser is kept so one request can be followed across
 * both tiers. Missing or malformed values are replaced with a fresh UUID; the
 * backend applies the same rule, so the header that reaches it is always safe
 * to use as a log key.
 */
export const CORRELATION_ID_HEADER = "X-Correlation-ID";

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Only `.get()` is needed, so route requests and next/headers both fit. */
type HeaderReader = Pick<Headers, "get">;

export function resolveCorrelationId(headers: HeaderReader): string {
  const supplied = headers.get(CORRELATION_ID_HEADER);
  if (supplied && UUID_RE.test(supplied)) {
    // Match the backend's canonical form: `str(UUID(value))` is lowercase.
    return supplied.toLowerCase();
  }
  return globalThis.crypto.randomUUID();
}

/** Spread these into an outbound fetch's headers. */
export function correlationHeaders(headers: HeaderReader): Record<string, string> {
  return { [CORRELATION_ID_HEADER]: resolveCorrelationId(headers) };
}
