/**
 * Nexus AI – client-side auth token store.
 *
 * The JWT access token is kept in a cookie so both browser-side calls
 * (lib/api.ts) and server-side proxies (/api/chat, /api/hitl) can forward
 * `Authorization: Bearer <token>` to the FastAPI backend.
 */

export const TOKEN_COOKIE = "nexus_access_token";

export function getAccessToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie
    .split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(`${TOKEN_COOKIE}=`));
  return match ? decodeURIComponent(match.slice(TOKEN_COOKIE.length + 1)) : null;
}

export function setAccessToken(token: string): void {
  if (typeof document === "undefined") return;
  document.cookie = `${TOKEN_COOKIE}=${encodeURIComponent(token)}; path=/; max-age=${
    60 * 60 * 24 * 7
  }; samesite=lax`;
}

export function clearAccessToken(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${TOKEN_COOKIE}=; path=/; max-age=0; samesite=lax`;
}