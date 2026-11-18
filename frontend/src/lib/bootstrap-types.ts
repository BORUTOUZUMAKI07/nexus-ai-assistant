/**
 * Wire contract for GET /api/bootstrap (additive BFF endpoint).
 *
 * Response is FLAT so one backend call can later replace the three calls here
 * without changing the browser contract:
 *
 *   GET /api/bootstrap?limit=50&offset=0&archived=false
 *   200 live: { authenticated: true, user: <UserResponse>,
 *     conversations: { items, total, page, size, next_cursor, has_more },
 *     settings: <UserSettings>,
 *     refreshed?: { access, refresh, expires_in?, refresh_expires_in? } }
 *   200 rejected: { authenticated: false }
 *   200 unreachable auth backend:
 *     { authenticated: false, unavailable: true,
 *       detail: "auth_unavailable", upstream_status: 503 }
 */
import type { Conversation, CurrentUser, UserSettings } from "./api";

// `authenticated: false` alone is a verdict; `unavailable: true` is NOT. The
// existing probe returns 503 on a cold start so the client cannot sign the
// user out by mistake; this contract keeps that signal plus Retry-After.
// A failing sub-result degrades inside its own object with the codebase's
// { detail, upstream_status } error shape.
// `refreshed.access` / `refreshed.refresh` are booleans reporting which
// httpOnly cookie was re-set. Token values never appear in the JSON body.

export interface BootstrapParams {
  limit?: number;
  offset?: number;
  archived?: boolean;
}

export const DEFAULT_BOOTSTRAP_PARAMS: Required<BootstrapParams> = {
  limit: 50,
  offset: 0,
  archived: false,
};

/** Same envelope the client's fetchConversations produces, with no new names. */
export interface BootstrapConversationPage {
  items: Conversation[];
  total: number;
  page: number;
  size: number;
  next_cursor: string | null;
  has_more: boolean;
  unavailable?: true;
  detail?: string;
  upstream_status?: number;
}

/** Booleans, not token values: the tokens stay in httpOnly cookies. */
export interface BootstrapRefreshed {
  access: boolean;
  refresh: boolean;
  expires_in?: number;
  refresh_expires_in?: number;
}

export type BootstrapSettings = UserSettings & {
  unavailable?: true;
  detail?: string;
  upstream_status?: number;
};

/** The auth verdict alone, before conversations/settings are attached. */
export type BootstrapAuth =
  | { authenticated: true; user: CurrentUser | null }
  | {
      authenticated: false;
      unavailable?: true;
      detail?: "auth_unavailable";
      upstream_status?: number;
    };

export type BootstrapResponse =
  | {
      authenticated: true;
      user: CurrentUser | null;
      conversations: BootstrapConversationPage;
      settings: BootstrapSettings;
      refreshed?: BootstrapRefreshed;
    }
  | {
      authenticated: false;
      unavailable?: true;
      detail?: "auth_unavailable";
      upstream_status?: number;
    };

/**
 * Deeper follow-up (documentation only — deliberately NOT implemented here;
 * a separate slice owns the backend):
 *
 *   POST /api/v1/bootstrap
 *   Request:  { "limit": 50, "offset": 0, "archived": false }
 *   Response: {
 *     "authenticated": true,
 *     "user": <UserResponse>,
 *     "conversations": {
 *       "items": [<Conversation>], "total": 1, "page": 1, "size": 50,
 *       "next_cursor": null, "has_more": false
 *     },
 *     "settings": <UserSettings>,
 *     "refreshed": { "access": true, "refresh": false,
 *                    "expires_in": 3600, "refresh_expires_in": 2592000 }
 *   }
 *   Rejected session: { "authenticated": false }
 *   Unavailable:      { "detail": "auth_unavailable", "upstream_status": 503 }
 *
 * The backend would resolve identity ONCE (one get_current_user instead of
 * three, removing 3x Redis blacklist + DB lookups) and return all three
 * payloads in one response, eliminating the remaining 3 Vercel-to-backend
 * hops. The browser contract above is already that response shape, so the
 * later slice only swaps the BFF internals.
 */
