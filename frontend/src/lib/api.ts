/**
 * Nexus AI SDK – Typed API client for the FastAPI backend.
 *
 * All requests go through Next.js App Router API routes (/api/*)
 * which proxy to the FastAPI backend and handle CORS, auth headers, etc.
 *
 * useChat from Vercel AI SDK handles the SSE streaming on the chat route.
 */

const API_BASE = "/api";
import { getAccessToken } from "./auth";

// ─── Types ──────────────────────────────────────────────────────────────────

export interface Conversation {
  id: string;
  title: string;
  mode: "normal" | "agent" | "code" | "research";
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ConversationPage {
  items: Conversation[];
  total: number;
  page: number;
  size: number;
}

export interface UsageStats {
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  total_cost_usd: number;
  request_count: number;
  period: string;
}

export interface KnowledgeFile {
  id: string;
  filename: string;
  status: "pending" | "processing" | "indexed" | "failed";
  size_bytes: number;
  chunk_count: number;
  created_at: string;
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

// ─── Conversations ───────────────────────────────────────────────────────────

export async function fetchConversations(
  page = 1,
  size = 20
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

export async function createConversation(
  title?: string,
  mode: Conversation["mode"] = "normal"
): Promise<Conversation> {
  const res = await fetch(`${API_BASE}/conversations`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ title: title ?? "New Conversation", mode }),
  });
  if (!res.ok) throw new Error(`Create conversation failed: ${res.status}`);
  return res.json();
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/conversations/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Delete conversation failed: ${res.status}`);
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
  if (!res.ok) throw new Error(`Delete file failed: ${res.status}`);
}

// ─── Usage ───────────────────────────────────────────────────────────────────

export async function fetchUsage(period = "30d"): Promise<UsageStats> {
  const res = await fetch(`${API_BASE}/usage/summary`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`Fetch usage failed: ${res.status}`);
  const data = await res.json();
  return { ...data, period: data.period ?? period };
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
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Registration failed" }));
    throw new Error(err.detail || `Registration failed: ${res.status}`);
  }
  return res.json();
}

export async function loginUser(payload: {
  email: string;
  password: string;
}): Promise<{ access_token: string; refresh_token: string; token_type: string }> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Invalid email or password" }));
    throw new Error(err.detail || `Login failed: ${res.status}`);
  }
  return res.json();
}
