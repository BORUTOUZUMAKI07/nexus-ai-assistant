/** Single source of truth for the selectable models (sidebar + command palette). */
export const MODELS = [
  { id: "llama-3.3-70b-versatile", name: "Llama 3.3 70B", provider: "Groq (Fast)" },
  { id: "deepseek-r1-distill-llama-70b", name: "DeepSeek R1", provider: "Reasoning" },
  { id: "llama-3.1-8b-instant", name: "Llama 3.1 8B", provider: "Groq (Instant)" },
] as const;
