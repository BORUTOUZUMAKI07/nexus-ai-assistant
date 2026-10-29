/**
 * The composer's toggles, resolved to the single `mode` the backend receives.
 *
 * Extracted from `app/app/page.tsx` so the mapping is testable on its own.
 *
 * The composer keeps Web / Code / Plan mutually exclusive (turning one on clears
 * the others), so in practice only one of `enableCode` / `enableWeb` is set. The
 * *priority* below is retained as the safety net for callers that do not go
 * through that UI -- including queued sends built before a toggle changed -- so
 * the single mode that travels is always the one the user saw lit: Code and Web
 * force their mode regardless of Fast/Deep, and the Fast/Deep toggle decides
 * only when neither is on. A combination that silently lost is how "Web" once
 * appeared active on a run the backend recorded as `code`.
 *
 * `fast` is a real backend mode: it answers with a single direct synthesizer
 * call and none of the agent machinery (no planner LLM, no routing, no
 * subagents, no tools, no RAG). Every other value runs the full graph.
 */
import type { ConversationMode } from "./api";

export interface ComposerModeOptions {
  enableCode?: boolean;
  enableWeb?: boolean;
  agentMode?: "fast" | "deep";
}

export function resolveConversationMode(options: ComposerModeOptions): ConversationMode {
  if (options.enableCode) return "code";
  if (options.enableWeb) return "research";
  // Default to the full graph when the toggle is absent: an explicitly "fast"
  // request is the only thing that opts out of the agent.
  return options.agentMode === "fast" ? "fast" : "agent";
}
