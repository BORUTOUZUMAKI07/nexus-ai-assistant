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

async function refreshAccessToken(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      // No body needed: the route handler reads the refresh token from its
      // httpOnly cookie. The browser attaches it automatically.
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!res.ok) return false;
      const data = (await res.json().catch(() => null)) as { ok?: boolean } | null;
      return Boolean(data?.ok);
    })().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
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