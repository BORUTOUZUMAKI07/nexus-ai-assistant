"use client";

import React, { useState, useEffect, useCallback } from "react";
import {
  Sliders,
  Key,
  Brain,
  Save,
  Trash2,
  CheckCircle2,
  Loader2,
  Plus,
  AlertCircle,
} from "lucide-react";
import {
  fetchSettings,
  updateSettings,
  fetchMemories,
  createMemory,
  deleteMemory,
  fetchAPIKeys,
  addAPIKey,
  UserSettings,
  UserMemory,
  APIKey,
} from "@/lib/api";

const MODELS = [
  { id: "llama-3.3-70b-versatile", name: "Llama 3.3 70B" },
  { id: "deepseek-r1-distill-llama-70b", name: "DeepSeek R1 Distill" },
  { id: "llama-3.1-8b-instant", name: "Llama 3.1 8B Instant" },
];

export const SettingsView: React.FC = () => {
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [memories, setMemories] = useState<UserMemory[]>([]);
  const [keys, setKeys] = useState<APIKey[]>([]);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);

  const [memoryContent, setMemoryContent] = useState("");
  const [memoryCategory, setMemoryCategory] = useState("preference");
  const [memoryBusy, setMemoryBusy] = useState(false);
  const [memorySearch, setMemorySearch] = useState("");
  const [memoryFilter, setMemoryFilter] = useState<string>("all");

  const [keyProvider, setKeyProvider] = useState("groq");
  const [keyValue, setKeyValue] = useState("");
  const [keyLabel, setKeyLabel] = useState("");
  const [keyBusy, setKeyBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [settingsData, memoriesData, keysData] = await Promise.all([
        fetchSettings(),
        fetchMemories(),
        fetchAPIKeys(),
      ]);
      setSettings(settingsData);
      setMemories(memoriesData);
      setKeys(keysData);
    } catch (err) {
      setLoadError(
        err instanceof Error ? err.message : "Could not load settings"
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const patch = (next: Partial<UserSettings>) =>
    setSettings((prev) => (prev ? { ...prev, ...next } : prev));

  const handleSave = async () => {
    if (!settings || saving) return;
    setSaving(true);
    try {
      const updated = await updateSettings({
        system_prompt_override: settings.system_prompt_override,
        default_model: settings.default_model,
        enable_memory: settings.enable_memory,
        enable_tools: settings.enable_tools,
        temperature: settings.temperature,
        max_tokens: settings.max_tokens,
      });
      setSettings(updated);
      setSavedNotice(true);
      setTimeout(() => setSavedNotice(false), 2500);
    } finally {
      setSaving(false);
    }
  };

  const handleAddMemory = async () => {
    if (!memoryContent.trim() || memoryBusy) return;
    setMemoryBusy(true);
    try {
      const created = await createMemory({
        content: memoryContent.trim(),
        category: memoryCategory,
      });
      setMemories((prev) => [created, ...prev]);
      setMemoryContent("");
    } finally {
      setMemoryBusy(false);
    }
  };

  const handleDeleteMemory = async (id: string) => {
    try {
      await deleteMemory(id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
    } catch (err) {
      console.warn("Delete memory failed:", err);
    }
  };

  const handleAddKey = async () => {
    if (!keyValue.trim() || keyBusy) return;
    setKeyBusy(true);
    try {
      const created = await addAPIKey({
        provider: keyProvider,
        key_value: keyValue.trim(),
        label: keyLabel.trim() || null,
      });
      setKeys((prev) => [created, ...prev]);
      setKeyValue("");
      setKeyLabel("");
    } finally {
      setKeyBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center gap-2 text-sm text-[var(--text-muted)]">
        <Loader2 className="w-4 h-4 animate-spin" />
        Loading settings…
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 p-8">
        <AlertCircle className="w-5 h-5 text-[var(--status-danger)]" />
        <p className="text-sm text-[var(--text-muted)]">{loadError}</p>
        <button
          onClick={load}
          className="rounded-lg border border-[var(--border-subtle)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:text-white transition-colors"
        >
          Retry
        </button>
      </div>
    );
  }

  // Computed filtered memories
  const filteredMemories = memories.filter((m) => {
    const matchesFilter = memoryFilter === "all" || m.category === memoryFilter;
    const matchesSearch = !memorySearch ||
      m.content.toLowerCase().includes(memorySearch.toLowerCase()) ||
      m.category.toLowerCase().includes(memorySearch.toLowerCase());
    return matchesFilter && matchesSearch;
  });

  return (
    <div className="flex-1 overflow-y-auto p-8 max-w-4xl mx-auto space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold tracking-tight text-white flex items-center gap-2">
            <Sliders className="w-5 h-5 text-[var(--accent)]" /> Settings
          </h2>
          <p className="text-xs text-[var(--text-muted)] mt-1">
            Agent configuration, Bring-Your-Own-Key providers, and persistent
            memories.
          </p>
        </div>
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] disabled:opacity-50 text-[var(--accent-foreground)] text-xs font-semibold shadow-sm transition-all"
        >
          {savedNotice ? (
            <CheckCircle2 className="w-4 h-4 text-[var(--accent-foreground)]" />
          ) : saving ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Save className="w-4 h-4" />
          )}
          <span>{savedNotice ? "Saved" : "Save changes"}</span>
        </button>
      </div>

      {/* Agent configuration */}
      <div className="glass-panel p-6 space-y-4">
        <h3 className="text-sm font-medium text-white border-b border-[var(--border-subtle)] pb-2">
          Agent configuration
        </h3>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="text-[11px] font-medium text-[var(--text-secondary)] block mb-1.5">
              Default model
            </label>
            <select
              value={settings?.default_model ?? ""}
              onChange={(e) => patch({ default_model: e.target.value })}
              className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] px-3 py-2 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
            >
              {MODELS.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="text-[11px] font-medium text-[var(--text-secondary)] block mb-1.5">
              Temperature
            </label>
            <input
              type="number"
              min={0}
              max={2}
              step={0.1}
              value={settings?.temperature ?? 0.7}
              onChange={(e) =>
                patch({ temperature: Number(e.target.value) })
              }
              className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] px-3 py-2 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent)] font-mono"
            />
          </div>
        </div>

        <div>
          <label className="text-[11px] font-medium text-[var(--text-secondary)] block mb-1.5">
            Global system instructions
          </label>
          <textarea
            rows={3}
            value={settings?.system_prompt_override ?? ""}
            onChange={(e) => patch({ system_prompt_override: e.target.value })}
            placeholder="Optional instructions applied to every conversation."
            className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] p-3 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent)] leading-relaxed resize-none placeholder:text-[var(--text-faint)]"
          />
        </div>

        <div className="flex flex-wrap gap-4 pt-1">
          {(
            [
              ["enable_memory", "Persistent memory"],
              ["enable_tools", "Agent tools"],
            ] as const
          ).map(([field, label]) => (
            <label
              key={field}
              className="flex items-center gap-2 text-xs text-[var(--text-secondary)] cursor-pointer"
            >
              <input
                type="checkbox"
                checked={Boolean(settings?.[field])}
                onChange={(e) => patch({ [field]: e.target.checked })}
                className="w-3.5 h-3.5 rounded border border-[var(--border-subtle)] bg-[var(--bg-main)] accent-[var(--accent)]"
              />
              {label}
            </label>
          ))}
        </div>
      </div>

      {/* BYOK API keys */}
      <div className="glass-panel p-6 space-y-4">
        <h3 className="text-sm font-medium text-white flex items-center gap-2 border-b border-[var(--border-subtle)] pb-2">
          <Key className="w-4 h-4 text-[var(--accent)]" /> Bring-Your-Own-Key
          providers
        </h3>
        <p className="text-xs text-[var(--text-muted)]">
          Add provider keys to use alongside the built-in free tier. Keys are
          stored encrypted and only a preview is ever shown.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
          <select
            value={keyProvider}
            onChange={(e) => setKeyProvider(e.target.value)}
            className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] px-3 py-2 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
          >
            <option value="groq">Groq</option>
            <option value="openrouter">OpenRouter</option>
            <option value="firecrawl">Firecrawl</option>
            <option value="e2b">E2B</option>
          </select>
          <input
            type="password"
            value={keyValue}
            onChange={(e) => setKeyValue(e.target.value)}
            placeholder="Provider API key"
            className="md:col-span-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] px-3 py-2 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent)] font-mono placeholder:text-[var(--text-faint)]"
          />
          <button
            onClick={handleAddKey}
            disabled={keyBusy || !keyValue.trim()}
            className="flex items-center justify-center gap-1.5 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] disabled:opacity-40 px-3 py-2 text-xs font-semibold text-[var(--accent-foreground)] shadow-sm transition-colors"
          >
            {keyBusy ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Plus className="w-3.5 h-3.5" />
            )}
            Add key
          </button>
        </div>

        {keys.length > 0 && (
          <div className="space-y-2">
            {keys.map((k) => (
              <div
                key={k.id}
                className="flex items-center justify-between rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] px-3 py-2 text-xs"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-mono text-[var(--accent)]">
                    {k.provider}
                  </span>
                  {k.label && (
                    <span className="text-[var(--text-muted)] truncate">
                      {k.label}
                    </span>
                  )}
                  <span className="font-mono text-[var(--text-faint)]">
                    {k.key_preview}
                  </span>
                </div>
                <span
                  className={`flex items-center gap-1 text-[10px] font-mono ${
                    k.is_active
                      ? "text-[var(--status-success)]"
                      : "text-[var(--status-danger)]"
                  }`}
                >
                  <CheckCircle2 className="w-3 h-3" />
                  {k.is_active ? "active" : "inactive"}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Persistent memories */}
      <div className="glass-panel p-6 space-y-4">
        <h3 className="text-sm font-medium text-white flex items-center gap-2 border-b border-[var(--border-subtle)] pb-2">
          <Brain className="w-4 h-4 text-[var(--accent)]" /> Persistent memories
          {memories.length > 0 && (
            <span className="ml-auto text-[10px] font-mono px-2 py-0.5 rounded-full bg-[var(--accent-soft)] text-[var(--accent-hover)] border border-[var(--accent)]/30">
              {memories.length} active
            </span>
          )}
        </h3>
        <p className="text-xs text-[var(--text-muted)]">
          Facts and preferences Nexus remembers across conversations.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
          <select
            value={memoryCategory}
            onChange={(e) => setMemoryCategory(e.target.value)}
            className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] px-3 py-2 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
          >
            <option value="preference">Preference</option>
            <option value="role">Role</option>
            <option value="fact">Fact</option>
            <option value="skill">Skill</option>
          </select>
          <input
            value={memoryContent}
            onChange={(e) => setMemoryContent(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAddMemory()}
            placeholder="E.g. Always respond with TypeScript examples"
            className="md:col-span-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] px-3 py-2 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent)] placeholder:text-[var(--text-faint)]"
          />
          <button
            onClick={handleAddMemory}
            disabled={memoryBusy || !memoryContent.trim()}
            className="flex items-center justify-center gap-1.5 rounded-lg border border-[var(--border-subtle)] px-3 py-2 text-xs text-[var(--text-secondary)] hover:text-white hover:border-[var(--border-strong)] disabled:opacity-40 transition-colors"
          >
            {memoryBusy ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Plus className="w-3.5 h-3.5" />
            )}
            Add
          </button>
        </div>

        {/* Search + Category filter pills */}
        {memories.length > 0 && (
          <div className="space-y-2">
            <input
              value={memorySearch}
              onChange={(e) => setMemorySearch(e.target.value)}
              placeholder="Search memories…"
              className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] px-3 py-2 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent)] placeholder:text-[var(--text-faint)]"
            />
            <div className="flex flex-wrap gap-1.5">
              {["all", "preference", "role", "fact", "skill"].map((cat) => (
                <button
                  key={cat}
                  type="button"
                  onClick={() => setMemoryFilter(cat)}
                  className={`px-2.5 py-1 rounded-full text-[10px] font-medium uppercase tracking-wide transition-colors ${
                    memoryFilter === cat
                      ? "bg-[var(--accent)] text-[var(--accent-foreground)]"
                      : "bg-[var(--bg-main)] border border-[var(--border-subtle)] text-[var(--text-muted)] hover:text-white"
                  }`}
                >
                  {cat}
                  {cat !== "all" && (
                    <span className="ml-1 opacity-60">
                      ({memories.filter((m) => m.category === cat).length})
                    </span>
                  )}
                </button>
              ))}
            </div>
          </div>
        )}

        {filteredMemories.length === 0 ? (
          <p className="text-xs text-[var(--text-muted)]">
            {memorySearch || memoryFilter !== "all" ? "No memories match your filter." : "No memories stored yet."}
          </p>
        ) : (
          <div className="space-y-2">
            {filteredMemories.map((m) => (
              <div
                key={m.id}
                className="p-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] flex items-center justify-between text-xs"
              >
                <div className="min-w-0">
                  <span className="text-[9px] uppercase px-1.5 py-0.5 rounded bg-[var(--accent-soft)] text-[var(--accent-hover)] font-mono mr-2">
                    {m.category}
                  </span>
                  <span className="text-[var(--text-secondary)]">{m.content}</span>
                </div>
                <button
                  onClick={() => handleDeleteMemory(m.id)}
                  className="text-[var(--text-faint)] hover:text-[var(--status-danger)] p-1 transition-colors"
                  title="Delete memory"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};