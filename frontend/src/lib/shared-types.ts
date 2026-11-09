// Shared types between frontend and backend.
// These mirror the Pydantic models in backend/app/domain/*/schemas.py.
// When a backend model changes, update both sides here to keep the contract
// in sync.
export interface Conversation {
  id: string;
  title: string;
  model: string | null;
  organization_id: string | null;
  system_prompt: string | null;
  created_at: string;
  updated_at: string;
  is_archived: boolean;
  message_count: number;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  thought_process: string | null;
  model: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  citations: Citation[];
  tool_calls: ToolCall[];
  user_feedback: string | null;
  created_at: string;
}

export interface Citation {
  id: string;
  message_id: string;
  source_type: "web" | "document" | "artifact" | "knowledge";
  source_id: string;
  filename: string;
  snippet: string;
  url: string | null;
  score: number | null;
  created_at: string;
}

export interface ToolCall {
  id: string;
  message_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown> | null;
  status: "running" | "completed" | "error";
  created_at: string;
}

export interface Artifact {
  id: string;
  user_id: string;
  conversation_id: string | null;
  type: "text" | "code" | "markdown" | "image" | "json";
  name: string;
  content: string;
  metadata: Record<string, unknown>;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ArtifactVersion {
  id: string;
  artifact_id: string;
  version: number;
  content: string;
  change_summary: string | null;
  created_at: string;
}

export interface User {
  id: string;
  email: string;
  username: string | null;
  role: "user" | "admin" | "owner";
  is_active: boolean;
  is_verified: boolean;
  created_at: string;
  updated_at: string;
}

export interface Org {
  id: string;
  name: string;
  slug: string;
  settings: Record<string, unknown>;
  created_at: string;
}

export interface UsageLog {
  id: string;
  user_id: string;
  organization_id: string | null;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  created_at: string;
}

export interface ApiKey {
  id: string;
  user_id: string;
  name: string;
  key_hash: string;
  scopes: string[];
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
}

export interface OAuthProvider {
  name: string;
  configured: boolean;
}

export interface HealthStatus {
  status: "ok" | "degraded" | "down";
  version: string;
  environment: string;
  services: Record<string, "ok" | "degraded" | "down">;
}

export interface StreamFrame {
  type:
    | "text_delta"
    | "tool_call"
    | "tool_result"
    | "thinking"
    | "citation"
    | "hitl_request"
    | "critique"
    | "quality"
    | "status"
    | "model_info"
    | "artifact"
    | "conversation_created"
    | "done"
    | "error";
  data?: Record<string, unknown>;
  content?: string;
  message?: string;
  content_type?: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  refresh_expires_in: number;
}

export interface AuthMeResponse {
  authenticated: boolean;
  user: User | null;
  unavailable: boolean;
}

export interface OAuthAuthorizationUrl {
  authorization_url: string;
  state: string;
}

export interface PlanItem {
  id: string;
  title: string;
  summary: string;
  steps: string[];
  status: "pending" | "approved" | "rejected" | "executed";
  created_at: string;
  updated_at: string;
}

export interface ShareLink {
  id: string;
  artifact_id: string;
  token: string;
  expires_at: string | null;
  created_at: string;
}

export interface FeedbackItem {
  id: string;
  message_id: string;
  rating: "thumbs_up" | "thumbs_down";
  comment: string | null;
  created_at: string;
}

export interface ModelInfo {
  id: string;
  name: string;
  group: "fast_chat" | "complex_reasoning";
  capabilities: string[];
  cost_per_1k_tokens: number;
}

export interface AuditLogEntry {
  id: string;
  user_id: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
}
