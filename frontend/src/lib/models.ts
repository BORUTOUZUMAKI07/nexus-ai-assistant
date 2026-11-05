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

/**
 * Tiers the router uses without being asked — shown wherever the two selectable
 * groups are shown, so the picker and the product's own "Model routing" story
 * do not disagree about how many models exist.
 *
 * These are deliberately **not selectable**. The backend whitelists exactly the
 * two group ids above (`resolve_answer_model`); a vision entry in the picker
 * would be a control that changes nothing, which is the same cosmetic-picker
 * trap the group ids replaced.
 */
export const AUTOMATIC_MODEL_TIERS = [
  {
    id: "vision",
    name: "Nexus Vision",
    provider: "Gemini 3.5 Flash · Google",
    reason: "Chosen automatically when you attach an image",
  },
] as const;

export interface ModelOption {
  id: string;
  name: string;
  provider: string;
  selectable: boolean;
  /** Why it cannot be picked; only present on the automatic tiers. */
  reason?: string;
}

/** Everything the UI may show in a model picker: selectable groups + automatic tiers. */
export const MODEL_CATALOG: ModelOption[] = [
  ...MODELS.map((m) => ({
    id: m.id as string,
    name: m.name,
    provider: m.provider,
    selectable: true,
  })),
  ...AUTOMATIC_MODEL_TIERS.map((m) => ({
    id: m.id,
    name: m.name,
    provider: m.provider,
    selectable: false,
    reason: m.reason,
  })),
];

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

/**
 * Legacy model strings that older `user_settings.default_model` rows still hold.
 *
 * Every one of these is a provider slug from before the picker was wired to the
 * router groups. They must map onto the *same* group the backend resolves them
 * to (`resolve_model_group` in `litellm_client.py`), which is why
 * `llama-3.3-70b-versatile` -- the old column default -- lands on
 * `complex_reasoning`. Without this map the settings dropdown showed a third
 * option reading `llama-3.3-70b-versatile` beside the two real groups (measured:
 * `modelDisplayName` has no label for it and returns the slug), and the chat
 * ignored the saved default entirely.
 */
const LEGACY_MODEL_IDS: Record<string, ModelId> = {
  "llama-3.3-70b-versatile": "complex_reasoning",
  "meta-llama/llama-3.3-70b-instruct:free": "complex_reasoning",
  "deepseek-r1-distill-llama-70b": "complex_reasoning",
  "llama-3.1-8b-instant": "fast_chat",
  "meta-llama/llama-3.1-8b-instruct:free": "fast_chat",
  "compound-mini": "fast_chat",
};

/**
 * Resolve any stored model string to a selectable group id, or `undefined` when
 * it is not one we know how to map. Unknown stays unknown on purpose: a value
 * this app cannot place should be shown as stored rather than silently rewritten
 * into a different model.
 */
export function canonicalModelId(
  model: string | undefined | null,
): ModelId | undefined {
  if (!model) return undefined;
  const known = MODELS.find((m) => m.id === model);
  if (known) return known.id;
  const bare = model.includes("/") ? model.slice(model.indexOf("/") + 1) : model;
  return LEGACY_MODEL_IDS[model] ?? LEGACY_MODEL_IDS[bare];
}

export function modelDisplayName(model: string | undefined): string | undefined {
  if (!model) return undefined;
  const group = MODELS.find((m) => m.id === model);
  if (group) return `${group.name} · ${group.provider}`;
  const bare = model.includes("/") ? model.slice(model.indexOf("/") + 1) : model;
  return PROVIDER_MODEL_LABELS[model] ?? PROVIDER_MODEL_LABELS[bare] ?? model;
}
