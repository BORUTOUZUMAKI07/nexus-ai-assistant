/**
 * Single source of truth for the selectable models (sidebar + command palette).
 *
 * The `id` is a backend **router group**, not a provider slug. That is the only
 * value the synthesis path can honor: the backend whitelists it
 * (`resolve_answer_model`) and routes through the group, so the selection is
 * real rather than cosmetic. The old entries here (`llama-3.3-70b-versatile`,
 * `deepseek-r1-distill-llama-70b`, `llama-3.1-8b-instant`) were hardcoded
 * labels the backend never read — the picker changed a string that was
 * discarded before the request left the browser.
 *
 * `provider` is display text only; it must stay in sync with the group's
 * primary deployment in `backend/app/infrastructure/ai/litellm_client.py`.
 */
export const MODELS = [
  {
    id: "complex_reasoning",
    name: "Nexus Reasoning",
    provider: "GPT-OSS 120B · Groq",
  },
  {
    id: "fast_chat",
    name: "Nexus Fast",
    provider: "Qwen 3.8 27B · Groq",
  },
] as const;

export type ModelId = (typeof MODELS)[number]["id"];

/** The model a fresh session starts on (previously the hardcoded synthesis default). */
export const DEFAULT_MODEL_ID: ModelId = "complex_reasoning";

/**
 * The routing tiers shown on the landing page's "Model routing" section.
 *
 * This is a *display* list, not the picker — it deliberately includes the
 * automatic vision tier, which a user never selects, so it is kept separate
 * from `MODELS`. The `model` slugs must match the primary deployment of each
 * router group in `backend/app/infrastructure/ai/litellm_client.py`; the
 * values that used to be inlined in `page.tsx` had drifted onto a removed
 * model (`groq/compound-mini`) and a slug that never existed
 * (`nvidia/nemotron-3:free`).
 */
export const ROUTING_TIERS = [
  {
    name: "Fast",
    model: "groq/qwen/qwen3.8-27b",
    provider: "Groq",
    latency: "~300ms",
    use: "Everyday chat and quick factual answers.",
  },
  {
    name: "Reasoning",
    model: "groq/openai/gpt-oss-120b",
    provider: "Groq",
    latency: "~2s",
    use: "Multi-step reasoning, research, and analysis.",
  },
  {
    name: "Vision",
    model: "gemini/gemini-3.5-flash",
    provider: "Google Gemini",
    latency: "~1s",
    use: "Reads images and synthesises long-context answers.",
  },
] as const;

/**
 * Friendly labels for the model strings the backend reports.
 *
 * The backend reports the **provider** string it read off the response
 * (`qwen/qwen3.8-27b`, sometimes `groq/`-prefixed) rather than the group id,
 * precisely so a provider fallback is visible instead of hidden behind the
 * picker's label. This maps the known strings back to a human name and falls
 * back to the raw string, so an unknown model is still shown rather than
 * silently blanked.
 */
const PROVIDER_MODEL_LABELS: Record<string, string> = {
  "qwen/qwen3.8-27b": "Nexus Fast · Qwen 3.8 27B",
  "openai/gpt-oss-120b": "Nexus Reasoning · GPT-OSS 120B",
  "gpt-oss-120b": "Nexus Reasoning · GPT-OSS 120B",
  "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": "Nemotron 30B (vision)",
  "liquid/lfm-2.5-2.6b:free": "Liquid 2.6B (fallback)",
  // Gemini answers whenever Groq is egress-blocked (the §9.39 fallback). litellm
  // reports `response.model` WITHOUT the provider prefix -- measured:
  // gemini/gemini-3.5-flash-lite -> "gemini-3.5-flash-lite" -- so the bare slugs
  // below are what the transcript actually receives. A provider-prefixed form
  // would still resolve through the bare-strip in `modelDisplayName`, so no
  // prefixed duplicate is kept here (an unreachable second entry is a second
  // place to drift).
  "gemini-3.5-flash": "Gemini 3.5 Flash (vision)",
  "gemini-3.5-flash-lite": "Gemini 3.5 Flash Lite (fallback)",
  "pollinations/flux": "Pollinations Flux",
  "pollinations/turbo": "Pollinations Turbo",
};

export function modelDisplayName(model: string | undefined): string | undefined {
  if (!model) return undefined;
  const group = MODELS.find((m) => m.id === model);
  if (group) return `${group.name} · ${group.provider}`;
  const bare = model.includes("/") ? model.slice(model.indexOf("/") + 1) : model;
  return PROVIDER_MODEL_LABELS[model] ?? PROVIDER_MODEL_LABELS[bare] ?? model;
}
