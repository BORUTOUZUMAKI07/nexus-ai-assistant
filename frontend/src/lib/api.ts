/**
 * Nexus AI SDK – Typed API client for the FastAPI backend.
 *
 * Every request hits an /api/* App Router route handler on the Next.js server.
 * Those handlers (src/app/api/...) forward the request to FastAPI with the JWT
 * read from the `nexus_access_token` cookie attached as
 * `Authorization: Bearer <token>` (see lib/proxy.ts), so the token never has
 * to travel in browser-initiated headers.
 */

const API_BASE = "/api";
import { clearSession, SESSION_EXPIRED_EVENT } from "./auth";

// ─── Types (mirror backend Pydantic schemas) ────────────────────────────────

export type ConversationMode = "normal" | "agent" | "code" | "research";

export interface Conversation {
  id: string;
  title: string;
  model: string;
  is_pinned: boolean;
  is_archived: boolean;
  token_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationPage {
  items: Conversation[];
  total: number;
  page: number;
  size: number;
}

export interface MessageCitation {
  filename?: string;
  chunk_index?: number;
  score?: number;
  content_snippet?: string;
  source?: string;
  snippet?: string;
}

export interface MessageToolCall {
  tool_name?: string;
  name?: string;
  tool_input?: Record<string, unknown>;
  args?: unknown;
  tool_call_id?: string;
  status?: string;
  result?: unknown;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  thought_process: string | null;
  model: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  citations: MessageCitation[];
  tool_calls: MessageToolCall[];
  user_feedback: string | null;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  system_prompt: string | null;
  messages: ConversationMessage[];
}

export interface UsageStats {
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  total_cost_usd: number;
  total_requests: number;
  average_latency_ms: number;
}

export interface KnowledgeFile {
  id: string;
  filename: string;
  original_filename?: string;
  status: "pending" | "processing" | "indexed" | "failed";
  size_bytes: number;
  chunk_count: number;
  error_message?: string | null;
  created_at: string;
}

export interface UserSettings {
  id: string;
  user_id: string;
  theme: string;
  default_model: string;
  system_prompt_override: string | null;
  temperature: number;
  max_tokens: number;
  stream_response: boolean;
  enable_memory: boolean;
  enable_tools: boolean;
  custom_settings: Record<string, unknown>;
}

export interface UserSettingsUpdate {
  theme?: string;
  default_model?: string;
  system_prompt_override?: string | null;
  temperature?: number;
  max_tokens?: number;
  stream_response?: boolean;
  enable_memory?: boolean;
  enable_tools?: boolean;
  custom_settings?: Record<string, unknown>;
}

export interface UserMemory {
  id: string;
  user_id: string;
  content: string;
  category: string;
  confidence: number;
  source_conversation_id: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface UserMemoryCreate {
  content: string;
  category?: string;
  confidence?: number;
  source_conversation_id?: string | null;
}

export interface APIKey {
  id: string;
  user_id: string;
  provider: string;
  key_preview: string;
  label: string | null;
  is_active: boolean;
  created_at: string;
}

export interface APIKeyCreate {
  provider: string;
  key_value: string;
  label?: string | null;
}

export interface HITLFeedback {
  threadId: string;
  action: "approve" | "reject" | "modify";
  data?: Record<string, unknown>;
}

// ─── Auth helpers ────────────────────────────────────────────────────────────

// The browser never holds a token: /api/* route handlers read the httpOnly
// access cookie server-side and attach Authorization themselves (lib/proxy.ts).
function authHeaders(extra?: Record<string, string>): Record<string, string> {
  return { ...(extra ?? {}) };
}

async function parseError(res: Response, fallback: string): Promise<Error> {
  const body = (await res.json().catch(() => null)) as
    | { detail?: string; error?: string }
    | null;
  return new Error(body?.detail ?? body?.error ?? fallback);
}

// Backend cold start (imports + schema sync) can take 30-60s, so gateway-style
// statuses returned by the /api/* proxy when the backend is momentarily
// unreachable (503) are retried with exponential backoff — letting the UI
// self-heal instead of showing a permanent error. The proxy turns a refused
// connection into a clean 503, so only HTTP gateway statuses are retried; a
// thrown fetch error means Next itself failed and fails fast. Auth/business
// errors (401/4xx) surface immediately.
const TRANSIENT_STATUS = new Set([429, 502, 503, 504]);
const DEFAULT_RETRY_ATTEMPTS = 6;
const DEFAULT_RETRY_BASE_DELAY_MS = 1000;
const RETRY_MAX_DELAY_MS = 16000;

export interface FetchRetryOptions {
  attempts?: number;
  baseDelayMs?: number;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ─── Silent session refresh ───────────────────────────────────────────────────
// The backend access token expires after ACCESS_TOKEN_EXPIRE_MINUTES and the
// refresh token is single-use (rotated on every exchange). When an
// authenticated call comes back 401, POST /api/auth/refresh — which reads the
// httpOnly refresh cookie server-side, exchanges it, and re-bakes both cookies
// — then retry the request once. Only one refresh may run at a time; if it
// fails the session is cleared and the app is bounced to /signin.

const NO_AUTO_REFRESH_PATHS = ["/api/auth/login", "/api/auth/register", "/api/auth/refresh"];

function isAuthRoute(url: string): boolean {
  return NO_AUTO_REFRESH_PATHS.some((path) => url.includes(path));
}

let refreshInFlight: Promise<boolean> | null = null;

// ─── Cross-tab refresh coordination ───────────────────────────────────────────
//
// refreshInFlight above only serialises refreshes *within* one tab. Two tabs
// whose access tokens expire together each fire their own refresh carrying the
// same httpOnly cookie, and the server cannot distinguish that from a stolen
// token being replayed -- so it revoked every session and the user was logged
// out of everything just for opening a second tab.
//
// A BroadcastChannel elects one refresher per browser. The other tabs wait for
// the result instead of calling the backend, which removes the race at the
// source. The server-side grace window (REFRESH_REUSE_GRACE_SECONDS) covers the
// cases this cannot: two devices, or a tab that dies mid-refresh.

const REFRESH_CHANNEL = "nexus:auth-refresh";
/** How long a follower waits for the leader to report success. */
const REFRESH_WAIT_MS = 5000;
/**
 * How long a contender collects claims before declaring itself leader.
 *
 * This must be comfortably longer than ``REFRESH_BEAT_MS``, and that relation is
 * the load-bearing invariant, not the absolute values. BroadcastChannel does not
 * replay, so a tab that opens after the leader already claimed never sees that
 * claim; it can only learn the lock is held by hearing a later heartbeat. If
 * the election were shorter than the beat, a tab that arrived just after a beat
 * would sit out the whole window in silence, conclude it won, and race the
 * leader -- reintroducing the double refresh this exists to prevent. With the
 * beat faster than the election, any tab that starts hears a heartbeat several
 * times over before it has to decide.
 *
 * The cost is one extra heartbeat interval of latency on a refresh that happens
 * roughly once an hour, in exchange for correctness.
 */
const REFRESH_ELECTION_MS = 150;
/** How often the leader re-announces that it still holds the lock. */
const REFRESH_BEAT_MS = 50;
/** How long a leader may hold the claim before followers assume it died. */
const REFRESH_LOCK_TTL_MS = 10_000;
/**
 * Identifies this tab for the lifetime of the document.
 *
 * It has two jobs: recognising the echo of our own messages, and being the base
 * of the nonce that separates two claims made in the same millisecond. A
 * collision between two tabs would make them each ignore the other's claim and
 * both refresh, so this wants real entropy -- 52 bits from `Math.random` is
 * plenty for two or three tabs in one browser, and the `claimSeq` suffix
 * removes any repeat within this tab.
 */
const TAB_ID = Math.random().toString(36).slice(2);
let claimSeq = 0;

interface RefreshMessage {
  /** "claim": I am contending. "done": the cookies are re-baked. */
  type: "claim" | "done";
  /** Unique per attempt, so a tab ignores the echo of its own message. */
  nonce: string;
  /**
   * When this refresh round started (ms since epoch). Carried on every claim
   * from the same round, heartbeats included, so it identifies the round rather
   * than the message. This is what makes leadership stable: a tab that joins
   * late sees the incumbent's *earlier* timestamp and stands down, whereas
   * comparing nonces would let a latecomer with a smaller nonce outrank a
   * leader that is already mid-exchange.
   */
  at: number;
}

/**
 * The channel, tagged with the nonce this tab won (if any). A property beats
 * threading a second return value through every step: the nonce is only needed
 * when announcing the result, and the channel is the thing that carries it.
 */
type RefreshChannel = BroadcastChannel & { __nonce: string; __at: number };

function nextNonce(): string {
  claimSeq += 1;
  return `${TAB_ID}:${claimSeq}`;
}

/**
 * Open a channel for electing this round's refresher, or null if this
 * environment cannot.
 *
 * Two separate failures are covered. During server rendering there is no browser
 * to coordinate with -- and `BroadcastChannel` does exist as a Node global, so
 * without the guard we would quietly open a channel to nobody and hold the event
 * loop open with it. In the browser, the constructor can be missing or can
 * refuse; the try/catch covers both, because an absent `BroadcastChannel` is just
 * a `ReferenceError` like any other. Checking `in window` beforehand would be a
 * second spelling of the same thing, and an untestable one.
 */
function openRefreshChannel(): RefreshChannel | null {
  if (typeof window === "undefined") return null;
  try {
    // __nonce starts empty and is filled in by runElection; the leader is the
    // only one that reads it back.
    return Object.assign(new BroadcastChannel(REFRESH_CHANNEL), {
      __nonce: "",
      __at: 0,
    }) as RefreshChannel;
  } catch {
    return null;
  }
}

async function refreshAccessToken(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = runRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

async function runRefresh(): Promise<boolean> {
  const ch = openRefreshChannel();
  let stopBeating: (() => void) | null = null;

  if (ch) {
    if (!(await runElection(ch))) {
      // Another tab is the leader. Wait for it to report rather than guessing:
      // claiming first is not the same as succeeding, and returning early would
      // retry the original request against a token that is still expired.
      const refreshed = await waitForPeerDone(ch);
      ch.close();
      return refreshed;
    }
    // We lead. Keep re-announcing until we are finished, so a tab that starts
    // *after* us learns the lock is held instead of racing us.
    stopBeating = startBeating(ch, ch.__nonce, ch.__at);
  }

  // No body needed: the route handler reads the refresh token from its
  // httpOnly cookie. The browser attaches it automatically.
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  const ok = res.ok
    ? Boolean(
        ((await res.json().catch(() => null)) as { ok?: boolean } | null)?.ok,
      )
    : false;
  stopBeating?.();
  if (ch && ok) {
    // Announce the result under *our* nonce. The tabs that stood down are
    // waiting on this message, and a nonce they never sent themselves is
    // unambiguous -- whereas a fixed "leader" tag would also match messages from
    // a different refresh round that happened to overlap this one.
    ch.postMessage({
      type: "done",
      nonce: ch.__nonce,
      at: ch.__at,
    } satisfies RefreshMessage);
  }
  ch?.close();
  return ok;
}

/**
 * Contend for the refresh lock. Returns true if this tab should do the
 * exchange, false if a peer is already handling it.
 *
 * The tie-break is the important part, and getting it wrong is worse than not
 * having it. Tabs waking together all broadcast a claim at the same moment and
 * each sees the others', so the obvious rule -- "someone else claimed, so I
 * stand down" -- makes *every* tab stand down and nobody refreshes at all,
 * turning a race into a deadlock. So leadership is a total order: the earliest
 * round wins, and the nonce only separates claims made in the same millisecond.
 * Exactly one tab ever proceeds.
 *
 * A peer that is already mid-exchange needs no special case -- its round started
 * earlier, so a tab joining now sees a smaller ``at`` and stands down.
 */
async function runElection(ch: RefreshChannel): Promise<boolean> {
  const nonce = nextNonce();
  // One timestamp per refresh round, reused on every heartbeat. It has to be
  // captured once: a leader that stamped each beat afresh would look newer than
  // a tab that joined after it, and that tab would refuse to defer to it.
  const at = Date.now();
  ch.__nonce = nonce;
  ch.__at = at;
  return new Promise<boolean>((resolve) => {
    let beaten = false;
    let settled = false;

    const finish = (result: boolean) => {
      if (settled) return;
      settled = true;
      clearTimeout(electionTimer);
      clearTimeout(lockTimer);
      ch.removeEventListener("message", onMessage);
      resolve(result);
    };

    function onMessage(event: MessageEvent) {
      const msg = event.data as RefreshMessage | null;
      if (!msg || msg.nonce === nonce) return;
      if (msg.type === "done") {
        // A leader already finished; nothing to contend for.
        finish(false);
        return;
      }
      // An *earlier* round outranks ours: that tab has been waiting longer, and
      // may already be mid-exchange. A later one is a tab that started after us
      // and will defer to us instead.
      //
      // This is why the ordering is by timestamp and not by nonce. A tab that
      // joins late with a small nonce would otherwise outrank a leader already
      // holding the lock, and both would refresh. Nonce only breaks ties within
      // the same millisecond, which is what genuinely simultaneous claims look
      // like, and a total order there means exactly one of them proceeds.
      const older = msg.at < at || (msg.at === at && msg.nonce < nonce);
      if (older) {
        beaten = true;
        finish(false);
      }
    }

    const electionTimer = setTimeout(() => finish(!beaten), REFRESH_ELECTION_MS);
    // If the leader dies between claiming and reporting, followers must not wait
    // forever: past the lock TTL, take over.
    const lockTimer = setTimeout(() => finish(!beaten), REFRESH_LOCK_TTL_MS);

    ch.addEventListener("message", onMessage);
    postClaim(ch, nonce, at);
  });
}

function postClaim(ch: RefreshChannel, nonce: string, at: number) {
  ch.postMessage({ type: "claim", nonce, at } satisfies RefreshMessage);
}

/**
 * Re-announce leadership on a timer until stopped.
 *
 * BroadcastChannel does not replay, so a tab that opens after we claimed never
 * sees that claim. Without the heartbeat it would time out its own election and
 * race us, reintroducing the exact double-refresh this exists to prevent. The
 * heartbeat also covers the leader's own channel being the only one open.
 */
function startBeating(ch: RefreshChannel, nonce: string, at: number): () => void {
  const timer = setInterval(() => {
    postClaim(ch, nonce, at);
  }, REFRESH_BEAT_MS);
  return () => clearInterval(timer);
}

/** Wait for the elected refresher to report that the cookies were re-baked. */
function waitForPeerDone(ch: RefreshChannel): Promise<boolean> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), REFRESH_WAIT_MS);
    ch.onmessage = (event: MessageEvent) => {
      if ((event.data as RefreshMessage | null)?.type === "done") {
        clearTimeout(timer);
        resolve(true);
      }
    };
  });
}

function notifySessionExpired(): void {
  if (typeof window === "undefined") return;
  try {
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
  } catch {
    // Event dispatch must never break the caller on exotic environments.
  }
}

async function nexusFetch(
  url: string,
  init: RequestInit | undefined,
  retried = false
): Promise<Response> {
  const res = await fetch(url, init);
  if (res.status === 401 && !retried && !isAuthRoute(url)) {
    if (await refreshAccessToken()) {
      // Cookies were re-baked server-side; retry the identical request.
      return nexusFetch(url, init, true);
    }
    // Terminal: refresh declined or refresh token already gone.
    await clearSession();
    notifySessionExpired();
  }
  return res;
}

async function fetchWithRetry(
  url: string,
  init: RequestInit | undefined,
  options: FetchRetryOptions = {}
): Promise<Response> {
  const attempts = Math.max(1, options.attempts ?? DEFAULT_RETRY_ATTEMPTS);
  const baseDelayMs = Math.max(
    0,
    options.baseDelayMs ?? DEFAULT_RETRY_BASE_DELAY_MS
  );
  let lastStatus = 503;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const res = await nexusFetch(url, init);
    if (!TRANSIENT_STATUS.has(res.status)) return res;
    lastStatus = res.status;
    if (attempt < attempts) {
      const delay = Math.min(
        baseDelayMs * 2 ** (attempt - 1),
        RETRY_MAX_DELAY_MS
      );
      await sleep(delay + Math.random() * 250);
    }
  }
  throw new Error(`Backend unavailable (${lastStatus})`);
}

// ─── Conversations ───────────────────────────────────────────────────────────

export async function fetchConversations(
  page = 1,
  size = 50,
  retry: FetchRetryOptions = {}
): Promise<ConversationPage> {
  const limit = size;
  const offset = (page - 1) * size;
  const res = await fetchWithRetry(
    `${API_BASE}/conversations?limit=${limit}&offset=${offset}&archived=false`,
    { headers: authHeaders() },
    retry
  );
  if (!res.ok) throw new Error(`Fetch conversations failed: ${res.status}`);
  const data: Conversation[] = await res.json();
  return { items: data, total: data.length, page, size };
}

export async function fetchConversation(
  id: string,
  retry: FetchRetryOptions = {}
): Promise<ConversationDetail> {
  const res = await fetchWithRetry(
    `${API_BASE}/conversations/${id}`,
    { headers: authHeaders() },
    retry
  );
  if (!res.ok) throw new Error(`Fetch conversation failed: ${res.status}`);
  return res.json();
}

export async function createConversation(
  titleOrOptions?: string | { title?: string; mode?: ConversationMode; model?: string },
  mode: ConversationMode = "normal",
  model?: string
): Promise<Conversation> {
  let title = "New Conversation";
  let convMode = mode;
  let convModel = model;
  if (typeof titleOrOptions === "object" && titleOrOptions !== null) {
    title = titleOrOptions.title ?? "New Conversation";
    convMode = titleOrOptions.mode ?? "normal";
    convModel = titleOrOptions.model;
  } else if (typeof titleOrOptions === "string") {
    title = titleOrOptions;
  }

  const res = await nexusFetch(`${API_BASE}/conversations`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      title,
      mode: convMode,
      ...(convModel ? { model: convModel } : {}),
    }),
  });
  if (!res.ok) throw new Error(`Create conversation failed: ${res.status}`);
  return res.json();
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await nexusFetch(`${API_BASE}/conversations/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`Delete conversation failed: ${res.status}`);
  }
}

export async function updateConversation(
  id: string,
  patch: { title?: string; is_pinned?: boolean; is_archived?: boolean }
): Promise<Conversation> {
  const res = await nexusFetch(`${API_BASE}/conversations/${id}`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Update conversation failed: ${res.status}`);
  return res.json();
}

export async function forkConversation(
  id: string,
  forkMessageId: string,
  branchName?: string
): Promise<Conversation> {
  const res = await nexusFetch(`${API_BASE}/conversations/${id}/fork`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      fork_message_id: forkMessageId,
      branch_name: branchName ?? "Forked Branch",
    }),
  });
  if (!res.ok) throw new Error(`Fork conversation failed: ${res.status}`);
  return res.json();
}

// ─── Knowledge / Files ───────────────────────────────────────────────────────

export async function fetchKnowledgeFiles(
  retry: FetchRetryOptions = {}
): Promise<KnowledgeFile[]> {
  const res = await fetchWithRetry(
    `${API_BASE}/files`,
    { headers: authHeaders() },
    retry
  );
  if (!res.ok) throw new Error(`Fetch files failed: ${res.status}`);
  return res.json();
}

export async function uploadFile(file: File): Promise<KnowledgeFile> {
  const form = new FormData();
  form.append("file", file);
  const res = await nexusFetch(`${API_BASE}/files/upload`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  return res.json();
}

export async function deleteFile(id: string): Promise<void> {
  const res = await nexusFetch(`${API_BASE}/files/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`Delete file failed: ${res.status}`);
  }
}

export async function ragQuery(query: string, topK = 5): Promise<{
  citations: MessageCitation[];
  query: string;
  took_ms?: number;
}> {
  const res = await nexusFetch(`${API_BASE}/files/rag/query`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ query, top_k: topK }),
  });
  if (!res.ok) throw new Error(`RAG query failed: ${res.status}`);
  return res.json();
}

// ─── Usage ───────────────────────────────────────────────────────────────────

export async function fetchUsage(): Promise<UsageStats> {
  const res = await nexusFetch(`${API_BASE}/usage/summary`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`Fetch usage failed: ${res.status}`);
  return res.json();
}

// ─── Settings ────────────────────────────────────────────────────────────────

export async function fetchSettings(): Promise<UserSettings> {
  const res = await nexusFetch(`${API_BASE}/settings`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`Fetch settings failed: ${res.status}`);
  return res.json();
}

export async function updateSettings(
  patch: UserSettingsUpdate
): Promise<UserSettings> {
  const res = await nexusFetch(`${API_BASE}/settings`, {
    method: "PUT",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Update settings failed: ${res.status}`);
  return res.json();
}

export async function fetchMemories(): Promise<UserMemory[]> {
  const res = await nexusFetch(`${API_BASE}/settings/memories`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Fetch memories failed: ${res.status}`);
  return res.json();
}

export async function createMemory(memory: UserMemoryCreate): Promise<UserMemory> {
  const res = await nexusFetch(`${API_BASE}/settings/memories`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(memory),
  });
  if (!res.ok) throw new Error(`Create memory failed: ${res.status}`);
  return res.json();
}

export async function deleteMemory(id: string): Promise<void> {
  const res = await nexusFetch(`${API_BASE}/settings/memories/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`Delete memory failed: ${res.status}`);
  }
}

export async function fetchAPIKeys(): Promise<APIKey[]> {
  const res = await nexusFetch(`${API_BASE}/settings/keys`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`Fetch API keys failed: ${res.status}`);
  return res.json();
}

export async function addAPIKey(key: APIKeyCreate): Promise<APIKey> {
  const res = await nexusFetch(`${API_BASE}/settings/keys`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(key),
  });
  if (!res.ok) throw new Error(`Add API key failed: ${res.status}`);
  return res.json();
}

// ─── Message Feedback ─────────────────────────────────────────────────────────

export async function sendMessageFeedback(
  conversationId: string,
  messageId: string,
  feedback: "thumbs_up" | "thumbs_down",
  note?: string
): Promise<{ status: string }> {
  const res = await nexusFetch(
    `${API_BASE}/conversations/${conversationId}/messages/${messageId}/feedback`,
    {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ feedback, feedback_note: note ?? null }),
    }
  );
  if (!res.ok) throw new Error(`Feedback failed: ${res.status}`);
  return res.json();
}

// ─── HITL ────────────────────────────────────────────────────────────────────

export async function sendHITLFeedback(
  feedback: HITLFeedback
): Promise<{ status: string }> {
  const res = await nexusFetch(`${API_BASE}/hitl`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(feedback),
  });
  if (!res.ok) throw new Error(`HITL feedback failed: ${res.status}`);
  return res.json();
}

// ─── Authentication ──────────────────────────────────────────────────────────

export interface CurrentUser {
  id?: string;
  email?: string;
  role?: string;
  [key: string]: unknown;
}

/**
 * Resolves the caller's session via /api/auth/me. The browser cannot read the
 * httpOnly access cookie, so this server-side probe is the source of truth for
 * "am I signed in" and which UI sections (e.g. Admin) are visible.
 */
export async function fetchCurrentUser(): Promise<{
  authenticated: boolean;
  user?: CurrentUser;
}> {
  const res = await fetch(`${API_BASE}/auth/me`, { method: "GET" });
  if (!res.ok) return { authenticated: false };
  const data = (await res.json().catch(() => null)) as {
    authenticated?: boolean;
    user?: CurrentUser;
  } | null;
  if (!data) return { authenticated: res.ok };
  return { authenticated: Boolean(data.authenticated), user: data.user };
}

export async function registerUser(payload: {
  email: string;
  username: string;
  password: string;
  full_name?: string;
}): Promise<{ id: string; email: string; username: string }> {
  const res = await nexusFetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw await parseError(res, "Registration failed");
  return res.json();
}

export async function loginUser(payload: {
  email: string;
  password: string;
}): Promise<{ ok: boolean }> {
  const res = await nexusFetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw await parseError(res, "Invalid email or password");
  return (await res.json()) as { ok: boolean };
}

/** The OAuth identity providers the sign-in page offers. */
export type OAuthProviderName = "google" | "github";

export interface SsoAuthorizationUrl {
  authorization_url: string;
  state: string;
  provider: string;
}

/**
 * Asks the server-side proxy for a provider's authorize URL. The page then
 * sends the whole browser to `authorization_url`; the provider bounces back to
 * /api/auth/oauth/{provider}/callback where the httpOnly session cookies are
 * written.
 */
export async function ssoLogin(provider: OAuthProviderName): Promise<SsoAuthorizationUrl> {
  const res = await nexusFetch(`${API_BASE}/auth/oauth/${provider}`, { method: "GET" });
  if (!res.ok) {
    throw await parseError(res, "Single sign-on is not configured.");
  }
  return (await res.json()) as SsoAuthorizationUrl;
}

// ─── Plans (plan-then-approve) ───────────────────────────────────────────────

export interface PlanItem {
  id: string;
  conversation_id: string;
  user_id: string;
  title: string;
  summary: string | null;
  steps: string[];
  status: "pending" | "approved" | "rejected";
  decision_reason: string | null;
  created_at: string;
  updated_at: string;
  decided_at: string | null;
}

/** Draft a plan for a task against a conversation (no tools run while drafting). */
export async function createPlan(
  conversationId: string,
  task: string
): Promise<PlanItem> {
  const res = await nexusFetch(`${API_BASE}/conversations/${conversationId}/plan`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ task }),
  });
  if (!res.ok) throw await parseError(res, "Plan creation failed");
  return res.json();
}

export async function fetchPlans(conversationId: string): Promise<PlanItem[]> {
  const res = await nexusFetch(`${API_BASE}/conversations/${conversationId}/plans`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Fetch plans failed: ${res.status}`);
  return res.json();
}

export async function approvePlan(planId: string): Promise<PlanItem> {
  const res = await nexusFetch(`${API_BASE}/plans/${planId}/approve`, {
    method: "POST",
    headers: authHeaders(),
  });
  if (!res.ok) throw await parseError(res, "Plan approval failed");
  return res.json();
}

export async function rejectPlan(
  planId: string,
  reason?: string
): Promise<PlanItem> {
  const res = await nexusFetch(`${API_BASE}/plans/${planId}/reject`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ reason: reason ?? null }),
  });
  if (!res.ok) throw await parseError(res, "Plan rejection failed");
  return res.json();
}

// ─── Artifacts (persisted + versioned) ───────────────────────────────────────

export interface ArtifactItem {
  id: string;
  user_id: string;
  conversation_id: string | null;
  message_id: string | null;
  title: string;
  language: string;
  mime_type: string;
  content: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ArtifactVersionItem {
  id: string;
  artifact_id: string;
  version: number;
  title: string;
  language: string;
  mime_type: string;
  content: string;
  created_at: string;
}

export interface ArtifactDetail extends ArtifactItem {
  versions: ArtifactVersionItem[];
}

export interface ArtifactCreateInput {
  title: string;
  content: string;
  language?: string;
  mime_type?: string;
  conversation_id?: string | null;
  message_id?: string | null;
}

export async function createArtifact(
  input: ArtifactCreateInput
): Promise<ArtifactItem> {
  const res = await nexusFetch(`${API_BASE}/artifacts`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(input),
  });
  if (!res.ok) throw await parseError(res, "Artifact save failed");
  return res.json();
}

export async function fetchArtifacts(
  conversationId?: string | null
): Promise<ArtifactItem[]> {
  const query = conversationId
    ? `?conversation_id=${encodeURIComponent(conversationId)}`
    : "";
  const res = await nexusFetch(`${API_BASE}/artifacts${query}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Fetch artifacts failed: ${res.status}`);
  return res.json();
}

export async function fetchArtifact(id: string): Promise<ArtifactDetail> {
  const res = await nexusFetch(`${API_BASE}/artifacts/${id}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Fetch artifact failed: ${res.status}`);
  return res.json();
}

export async function addArtifactVersion(
  id: string,
  content: string,
  patch?: { title?: string; language?: string }
): Promise<ArtifactItem> {
  const res = await nexusFetch(`${API_BASE}/artifacts/${id}/versions`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ content, ...(patch ?? {}) }),
  });
  if (!res.ok) throw await parseError(res, "Version save failed");
  return res.json();
}

export async function deleteArtifact(id: string): Promise<void> {
  const res = await nexusFetch(`${API_BASE}/artifacts/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`Delete artifact failed: ${res.status}`);
  }
}

// ─── Admin: lifecycle hooks ──────────────────────────────────────────────────

export interface HookPolicyItem {
  id: string;
  name: string;
  tool_name: string;
  event: "pre_tool" | "post_tool";
  org_id: string | null;
  action: "block" | "redact" | "log";
  field: string | null;
  message: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface HookPolicyInput {
  name: string;
  tool_name: string;
  event?: "pre_tool" | "post_tool";
  org_id?: string | null;
  action: "block" | "redact" | "log";
  field?: string | null;
  message?: string | null;
  enabled?: boolean;
}

export async function fetchHookPolicies(): Promise<HookPolicyItem[]> {
  const res = await nexusFetch(`${API_BASE}/admin/hooks`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`Fetch hooks failed: ${res.status}`);
  return res.json();
}

export async function createHookPolicy(
  input: HookPolicyInput
): Promise<HookPolicyItem> {
  const res = await nexusFetch(`${API_BASE}/admin/hooks`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(input),
  });
  if (!res.ok) throw await parseError(res, "Hook create failed");
  return res.json();
}

export async function updateHookPolicy(
  id: string,
  patch: Partial<HookPolicyInput>
): Promise<HookPolicyItem> {
  const res = await nexusFetch(`${API_BASE}/admin/hooks/${id}`, {
    method: "PUT",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw await parseError(res, "Hook update failed");
  return res.json();
}

export async function deleteHookPolicy(id: string): Promise<void> {
  const res = await nexusFetch(`${API_BASE}/admin/hooks/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`Hook delete failed: ${res.status}`);
  }
}

// ─── Admin: slice monitoring / fairness / bandits ───────────────────────────

export interface SliceRow {
  model: string;
  provider: string;
  volume: number;
  rank: number;
  popularity_bucket: string;
  error_rate: number;
  avg_latency_ms: number;
  cost_usd: number;
  helpful_rate: number | null;
  flags: string[];
}

export interface SliceReport {
  overall: {
    requests: number;
    error_rate: number;
    helpful_rate: number | null;
    avg_latency_ms: number;
    combined_cost_usd: number;
  };
  slices: SliceRow[];
  popularity_flags: {
    slice: string;
    helpful_rate: number | null;
    flags: string[];
  }[];
  error?: string;
}

export interface FairnessReport {
  evaluator_parity: {
    group: string;
    count: number;
    pass_rate: number;
  }[];
  model_parity: {
    model: string;
    count: number;
    pass_rate: number;
  }[];
  provider_error_parity: {
    provider: string;
    count: number;
    error_rate: number;
    flagged: boolean;
  }[];
  limitations: string;
  error?: string;
}

export interface BanditStatRow {
  experiment: string;
  variant: string;
  reward_count: number;
  mean_reward: number;
}

export interface BanditStatus {
  epsilon: number;
  exploration: string;
  stats: BanditStatRow[];
}

export interface OptimizationRunItem {
  id: string;
  prompt_key: string;
  status: string;
  candidate_count: number;
  accepted_variant: string;
  baseline_score: number;
  best_score: number;
  average_score: number;
  promoted: boolean;
  created_at: string | null;
}

export interface OptimizationRunRequest {
  prompt_key: string;
  baseline_prompt: string;
  cases?: { input: string; ideal?: string }[];
  candidate_count?: number;
}

export interface AuditReport {
  controls: {
    pii_redaction_enabled: boolean;
    response_cache_enabled: boolean;
    rate_limit_per_minute: number;
    totp_available: boolean;
  };
  lifecycle_hooks: { policy_count: number; enabled: number; block_policies: number };
  model_provenance: {
    model: string;
    requests: number;
    providers: string[];
    experiment_variants_seen: string[];
  }[];
  prompt_provenance: { version_count: number; latest_timestamp: string | null };
  red_team: {
    run_count: number;
    last_run_at?: string | null;
    total_probes?: number;
    blocked_probes?: number;
    defense_rate?: number;
    note?: string;
  };
  gdpr: { gdpr_export: number; gdpr_erasure: number };
  evaluations: { evaluator: string; count: number; pass_rate: number }[];
  retention: string;
  eu_ai_act: {
    classification: string;
    high_risk_articles: string;
    transparency_obligations: Record<string, string>;
    gpaI_models: Record<string, string>;
    fines: string;
    internal_evidence: Record<string, string>;
  };
}

export interface RedTeamRunItem {
  id: string;
  created_at: string | null;
  total_probes: number;
  blocked_probes: number;
  defense_rate: number;
  probe_count: number;
}

export async function fetchSliceReport(retry: FetchRetryOptions = {}): Promise<SliceReport> {
  const res = await fetchWithRetry(
    `${API_BASE}/admin/monitoring/slices`,
    { headers: authHeaders() },
    retry
  );
  if (!res.ok) throw new Error(`Slice report failed: ${res.status}`);
  return res.json();
}

export async function fetchFairnessReport(retry: FetchRetryOptions = {}): Promise<FairnessReport> {
  const res = await fetchWithRetry(
    `${API_BASE}/admin/monitoring/fairness`,
    { headers: authHeaders() },
    retry
  );
  if (!res.ok) throw new Error(`Fairness report failed: ${res.status}`);
  return res.json();
}

export async function fetchBanditStatus(retry: FetchRetryOptions = {}): Promise<BanditStatus> {
  const res = await fetchWithRetry(
    `${API_BASE}/admin/monitoring/bandits`,
    { headers: authHeaders() },
    retry
  );
  if (!res.ok) throw new Error(`Bandit status failed: ${res.status}`);
  return res.json();
}

// ─── Admin: prompt-optimization evidence trail ──────────────────────────────

export async function fetchOptimizationRuns(
  retry: FetchRetryOptions = {}
): Promise<OptimizationRunItem[]> {
  const res = await fetchWithRetry(
    `${API_BASE}/admin/optimization/runs`,
    { headers: authHeaders() },
    retry
  );
  if (!res.ok) throw new Error(`Optimization runs failed: ${res.status}`);
  return res.json();
}

export async function triggerOptimizationRun(
  input: OptimizationRunRequest
): Promise<OptimizationRunItem> {
  const res = await nexusFetch(`${API_BASE}/admin/optimization/run`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(input),
  });
  if (!res.ok) throw await parseError(res, "Optimization run failed");
  return res.json();
}

// ─── Admin: responsible-ML / compliance audit surface ───────────────────────

export async function fetchAuditReport(retry: FetchRetryOptions = {}): Promise<AuditReport> {
  const res = await fetchWithRetry(
    `${API_BASE}/admin/audit`,
    { headers: authHeaders() },
    retry
  );
  if (!res.ok) throw new Error(`Audit report failed: ${res.status}`);
  return res.json();
}

export async function fetchRedTeamRuns(retry: FetchRetryOptions = {}): Promise<RedTeamRunItem[]> {
  const res = await fetchWithRetry(
    `${API_BASE}/admin/audit/redteam`,
    { headers: authHeaders() },
    retry
  );
  if (!res.ok) throw new Error(`Red-team runs failed: ${res.status}`);
  const data = (await res.json()) as { runs: RedTeamRunItem[] };
  return data.runs;
}