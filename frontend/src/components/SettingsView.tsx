"use client";

import React, { useState } from "react";
import { Sliders, Key, Brain, Save, Trash2, CheckCircle2 } from "lucide-react";

export const SettingsView: React.FC = () => {
  const [groqKey, setGroqKey] = useState("gsk_••••••••••••••••");
  const [openrouterKey, setOpenrouterKey] = useState("");
  const [firecrawlKey, setFirecrawlKey] = useState("");
  const [e2bKey, setE2bKey] = useState("");
  const [systemPrompt, setSystemPrompt] = useState(
    "You are Nexus AI, an advanced, highly capable, and transparent AI assistant. Strictly adhere to factual accuracy, domain expertise, and rigorous logic."
  );

  const [memories, setMemories] = useState([
    { id: "1", category: "preference", content: "Prefers Python and TypeScript for code examples." },
    { id: "2", category: "role", content: "Software architect building high-performance distributed systems." },
  ]);

  const [savedNotice, setSavedNotice] = useState(false);

  const handleSave = () => {
    setSavedNotice(true);
    setTimeout(() => setSavedNotice(false), 2500);
  };

  const deleteMemory = (id: string) => {
    setMemories((prev) => prev.filter((m) => m.id !== id));
  };

  return (
    <div className="flex-1 overflow-y-auto p-8 max-w-4xl mx-auto space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-white flex items-center gap-2">
            <Sliders className="w-5 h-5 text-violet-400" /> Settings & Personalization
          </h2>
          <p className="text-xs text-neutral-400 mt-1">
            Manage your Bring-Your-Own-Key (BYOK) secrets, system prompt, and persistent memories.
          </p>
        </div>
        <button
          onClick={handleSave}
          className="flex items-center gap-2 px-4 py-2 rounded-xl bg-violet-600 hover:bg-violet-500 text-white text-xs font-medium transition-all shadow-md shadow-violet-600/20"
        >
          {savedNotice ? <CheckCircle2 className="w-4 h-4 text-emerald-300" /> : <Save className="w-4 h-4" />}
          <span>{savedNotice ? "Saved!" : "Save Settings"}</span>
        </button>
      </div>

      {/* BYOK API Keys */}
      <div className="glass-panel p-6 space-y-4">
        <h3 className="text-sm font-medium text-white flex items-center gap-2 border-b border-[var(--border-subtle)] pb-2">
          <Key className="w-4 h-4 text-violet-400" /> Bring Your Own Key (BYOK) Encryption Vault
        </h3>
        <p className="text-xs text-neutral-400">
          Keys are encrypted client-side and at rest using AES-256-GCM. Free-tier accounts have built-in shared keys.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
          <div>
            <label className="text-[11px] font-medium text-neutral-300 block mb-1">Groq API Key (Primary)</label>
            <input
              type="password"
              value={groqKey}
              onChange={(e) => setGroqKey(e.target.value)}
              placeholder="gsk_..."
              className="w-full bg-[#080a11] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-xs text-white outline-none focus:border-violet-500 font-mono"
            />
          </div>

          <div>
            <label className="text-[11px] font-medium text-neutral-300 block mb-1">OpenRouter Key (Fallback)</label>
            <input
              type="password"
              value={openrouterKey}
              onChange={(e) => setOpenrouterKey(e.target.value)}
              placeholder="sk-or-..."
              className="w-full bg-[#080a11] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-xs text-white outline-none focus:border-violet-500 font-mono"
            />
          </div>

          <div>
            <label className="text-[11px] font-medium text-neutral-300 block mb-1">Firecrawl Key (Web Search)</label>
            <input
              type="password"
              value={firecrawlKey}
              onChange={(e) => setFirecrawlKey(e.target.value)}
              placeholder="fc-..."
              className="w-full bg-[#080a11] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-xs text-white outline-none focus:border-violet-500 font-mono"
            />
          </div>

          <div>
            <label className="text-[11px] font-medium text-neutral-300 block mb-1">E2B API Key (Code Sandbox)</label>
            <input
              type="password"
              value={e2bKey}
              onChange={(e) => setE2bKey(e.target.value)}
              placeholder="e2b_..."
              className="w-full bg-[#080a11] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-xs text-white outline-none focus:border-violet-500 font-mono"
            />
          </div>
        </div>
      </div>

      {/* System Prompt Custom Instructions */}
      <div className="glass-panel p-6 space-y-3">
        <h3 className="text-sm font-medium text-white border-b border-[var(--border-subtle)] pb-2">
          Global System Instructions
        </h3>
        <textarea
          rows={3}
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          className="w-full bg-[#080a11] border border-[var(--border-subtle)] rounded-xl p-3 text-xs text-neutral-200 outline-none focus:border-violet-500 leading-relaxed resize-none"
        />
      </div>

      {/* Persistent User Memories */}
      <div className="glass-panel p-6 space-y-4">
        <h3 className="text-sm font-medium text-white flex items-center gap-2 border-b border-[var(--border-subtle)] pb-2">
          <Brain className="w-4 h-4 text-cyan-400" /> Persistent User Memories
        </h3>
        <p className="text-xs text-neutral-400">
          Nexus automatically remembers your key preferences across sessions.
        </p>

        <div className="space-y-2">
          {memories.map((m) => (
            <div key={m.id} className="p-3 rounded-xl bg-[#080a11] border border-[var(--border-subtle)] flex items-center justify-between text-xs">
              <div>
                <span className="text-[9px] uppercase px-1.5 py-0.5 rounded bg-cyan-950 text-cyan-400 font-mono mr-2">
                  {m.category}
                </span>
                <span className="text-neutral-200">{m.content}</span>
              </div>
              <button onClick={() => deleteMemory(m.id)} className="text-neutral-400 hover:text-rose-400 p-1">
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
