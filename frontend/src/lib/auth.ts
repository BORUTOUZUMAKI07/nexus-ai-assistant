/**
 * Nexus AI – client-side auth token store.
 *
 * The JWT access token is kept in a cookie so both browser-side calls
 * (lib/api.ts) and server-side proxies (/api/chat, /api/hitl) can forward
 * `Authorization: Bearer <token>` to the FastAPI backend.
 */

export const TOKEN_COOKIE = "nexus_access_token";
export const REFRESH_COOKIE = "nexus_refresh_token";
export const SESSION_EXPIRED_EVENT = "nexus:session-expired";

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

export function getRefreshToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie
    .split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(`${REFRESH_COOKIE}=`));
  return match ? decodeURIComponent(match.slice(REFRESH_COOKIE.length + 1)) : null;
}

export function setRefreshToken(token: string): void {
  if (typeof document === "undefined") return;
  document.cookie = `${REFRESH_COOKIE}=${encodeURIComponent(token)}; path=/; max-age=${
    60 * 60 * 24 * 7
  }; samesite=lax`;
}

export function clearRefreshToken(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${REFRESH_COOKIE}=; path=/; max-age=0; samesite=lax`;
}

export function setSession(accessToken: string, refreshToken: string): void {
  setAccessToken(accessToken);
  setRefreshToken(refreshToken);
}

export function clearSession(): void {
  clearAccessToken();
  clearRefreshToken();
}