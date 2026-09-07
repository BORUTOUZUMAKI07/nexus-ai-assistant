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
import { getAccessToken } from "./auth";

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
  status: "pending" | "processing" | "indexed" | "failed";
  size_bytes: number;
  chunk_count: number;
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

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...(extra ?? {}) };
  const token = getAccessToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

async function parseError(res: Response, fallback: string): Promise<Error> {
  const body = (await res.json().catch(() => null)) as
    | { detail?: string; error?: string }
    | null;
  return new Error(body?.detail ?? body?.error ?? fallback);
}

// ─── Conversations ───────────────────────────────────────────────────────────

export async function fetchConversations(
  page = 1,
  size = 50
): Promise<ConversationPage> {
  const limit = size;
  const offset = (page - 1) * size;
  const res = await fetch(
    `${API_BASE}/conversations?limit=${limit}&offset=${offset}&archived=false`,
    { headers: authHeaders() }
  );
  if (!res.ok) throw new Error(`Fetch conversations failed: ${res.status}`);
  const data: Conversation[] = await res.json();
  return { items: data, total: data.length, page, size };
}

export async function fetchConversation(id: string): Promise<ConversationDetail> {
  const res = await fetch(`${API_BASE}/conversations/${id}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Fetch conversation failed: ${res.status}`);
  return res.json();
}

export async function createConversation(
  title?: string,
  mode: ConversationMode = "normal",
  model?: string
): Promise<Conversation> {
  const res = await fetch(`${API_BASE}/conversations`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      title: title ?? "New Conversation",
      mode,
      ...(model ? { model } : {}),
    }),
  });
  if (!res.ok) throw new Error(`Create conversation failed: ${res.status}`);
  return res.json();
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/conversations/${id}`, {
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
  const res = await fetch(`${API_BASE}/conversations/${id}`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Update conversation failed: ${res.status}`);
  return res.json();
}

// ─── Knowledge / Files ───────────────────────────────────────────────────────

export async function fetchKnowledgeFiles(): Promise<KnowledgeFile[]> {
  const res = await fetch(`${API_BASE}/files`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`Fetch files failed: ${res.status}`);
  return res.json();
}

export async function uploadFile(file: File): Promise<KnowledgeFile> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/files/upload`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  return res.json();
}

export async function deleteFile(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/files/${id}`, {
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
  const res = await fetch(`${API_BASE}/files/rag/query`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ query, top_k: topK }),
  });
  if (!res.ok) throw new Error(`RAG query failed: ${res.status}`);
  return res.json();
}

// ─── Usage ───────────────────────────────────────────────────────────────────

export async function fetchUsage(): Promise<UsageStats> {
  const res = await fetch(`${API_BASE}/usage/summary`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`Fetch usage failed: ${res.status}`);
  return res.json();
}

// ─── Settings ────────────────────────────────────────────────────────────────

export async function fetchSettings(): Promise<UserSettings> {
  const res = await fetch(`${API_BASE}/settings`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`Fetch settings failed: ${res.status}`);
  return res.json();
}

export async function updateSettings(
  patch: UserSettingsUpdate
): Promise<UserSettings> {
  const res = await fetch(`${API_BASE}/settings`, {
    method: "PUT",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Update settings failed: ${res.status}`);
  return res.json();
}

export async function fetchMemories(): Promise<UserMemory[]> {
  const res = await fetch(`${API_BASE}/settings/memories`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Fetch memories failed: ${res.status}`);
  return res.json();
}

export async function createMemory(memory: UserMemoryCreate): Promise<UserMemory> {
  const res = await fetch(`${API_BASE}/settings/memories`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(memory),
  });
  if (!res.ok) throw new Error(`Create memory failed: ${res.status}`);
  return res.json();
}

export async function deleteMemory(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/settings/memories/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`Delete memory failed: ${res.status}`);
  }
}

export async function fetchAPIKeys(): Promise<APIKey[]> {
  const res = await fetch(`${API_BASE}/settings/keys`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`Fetch API keys failed: ${res.status}`);
  return res.json();
}

export async function addAPIKey(key: APIKeyCreate): Promise<APIKey> {
  const res = await fetch(`${API_BASE}/settings/keys`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(key),
  });
  if (!res.ok) throw new Error(`Add API key failed: ${res.status}`);
  return res.json();
}

// ─── HITL ────────────────────────────────────────────────────────────────────

export async function sendHITLFeedback(
  feedback: HITLFeedback
): Promise<{ status: string }> {
  const res = await fetch(`${API_BASE}/hitl`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(feedback),
  });
  if (!res.ok) throw new Error(`HITL feedback failed: ${res.status}`);
  return res.json();
}

// ─── Authentication ──────────────────────────────────────────────────────────

export async function registerUser(payload: {
  email: string;
  username: string;
  password: string;
  full_name?: string;
}): Promise<{ id: string; email: string; username: string }> {
  const res = await fetch(`${API_BASE}/auth/register`, {
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
}): Promise<{
  access_token: string;
  refresh_token: string;
  token_type: string;
}> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw await parseError(res, "Invalid email or password");
  return res.json();
}