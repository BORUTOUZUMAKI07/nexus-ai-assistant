/**
 * The composer's toggles, resolved to the single `mode` the backend receives.
 *
 * Extracted from `app/app/page.tsx` so the mapping is testable on its own. Two
 * toggles can be on at once and only one mode travels, so the *priority* is
 * load-bearing: Code and Web force their mode regardless of Fast/Deep, and the
 * Fast/Deep toggle decides only when neither is on.
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
