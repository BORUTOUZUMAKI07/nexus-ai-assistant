"use client";

/**
 * What the agent can actually do, read from the backend.
 *
 * `/api/v1/tools` and `/api/v1/prompts/skills` were fully served but had no BFF
 * route and no UI, so the product could not tell a user which tools the agent
 * has or which skills are compiled into its system prompt. Read-only by design:
 * skills are seeded and enabled server-side.
 */

import React, { useEffect, useState } from "react";
import { AlertCircle, Loader2, Sparkles, Wrench } from "lucide-react";
import {
  fetchAgentSkills,
  fetchAgentTools,
  type AgentSkill,
  type AgentTool,
} from "@/lib/api";

/** Tool payloads vary by source (plain dict or OpenAI-style nested `function`). */
function toolName(tool: AgentTool & { function?: { name?: string } }): string {
  return tool.name || tool.function?.name || "unnamed";
}

function toolDescription(
  tool: AgentTool & { function?: { description?: string } },
): string {
  return tool.description || tool.function?.description || "";
}

export const AgentCapabilities: React.FC = () => {
  const [tools, setTools] = useState<AgentTool[]>([]);
  const [skills, setSkills] = useState<AgentSkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    // Both reads are independent: a skills failure must not hide the tools.
    void Promise.allSettled([fetchAgentTools(), fetchAgentSkills()]).then(
      ([toolsResult, skillsResult]) => {
        if (!live) return;
        if (toolsResult.status === "fulfilled") setTools(toolsResult.value);
        if (skillsResult.status === "fulfilled") setSkills(skillsResult.value);
        if (
          toolsResult.status === "rejected" &&
          skillsResult.status === "rejected"
        ) {
          setError("Could not load the agent's capabilities.");
        }
        setLoading(false);
      },
    );
    return () => {
      live = false;
    };
  }, []);

  return (
    <div className="glass-panel p-6 space-y-4">
      <h3 className="text-sm font-medium text-[var(--text-primary)] flex items-center gap-2 border-b border-[var(--border-subtle)] pb-2">
        <Wrench className="w-4 h-4 text-[var(--accent-ink)]" /> Agent capabilities
        {tools.length > 0 && (
          <span className="ml-auto text-[10px] font-mono px-2 py-0.5 rounded-full bg-[var(--accent-soft)] text-[var(--accent-ink)] border border-[var(--accent)]/30">
            {tools.length} tools
          </span>
        )}
      </h3>
      <p className="text-xs text-[var(--text-muted)]">
        Tools the agent may call, and the skills compiled into its system prompt.
      </p>

      {loading ? (
        <p className="flex items-center gap-1.5 text-xs text-[var(--text-faint)]">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading…
        </p>
      ) : error ? (
        <p className="flex items-center gap-1.5 text-xs text-[var(--status-danger)]">
          <AlertCircle className="w-3.5 h-3.5" /> {error}
        </p>
      ) : (
        <div className="space-y-4">
          <div>
            <h4 className="text-[11px] uppercase tracking-wide text-[var(--text-faint)] mb-2">
              Tools
            </h4>
            {tools.length === 0 ? (
              <p className="text-xs text-[var(--text-faint)]">No tools reported.</p>
            ) : (
              <ul className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {tools.map((tool) => (
                  <li
                    key={toolName(tool)}
                    className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] p-2.5"
                  >
                    <p className="text-xs font-mono text-[var(--text-primary)]">
                      {toolName(tool)}
                    </p>
                    {toolDescription(tool) && (
                      <p className="text-[10px] text-[var(--text-muted)] mt-0.5 line-clamp-2">
                        {toolDescription(tool)}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div>
            <h4 className="text-[11px] uppercase tracking-wide text-[var(--text-faint)] mb-2">
              Skills
            </h4>
            {skills.length === 0 ? (
              <p className="text-xs text-[var(--text-faint)]">
                No skills enabled.
              </p>
            ) : (
              <ul className="space-y-2">
                {skills.map((skill) => (
                  <li
                    key={skill.id}
                    className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] p-2.5"
                  >
                    <p className="text-xs font-medium text-[var(--text-primary)] flex items-center gap-1.5">
                      <Sparkles className="w-3 h-3 text-[var(--accent-ink)]" />
                      {skill.name}
                      <span className="text-[9px] uppercase px-1 rounded bg-[var(--accent-soft)] text-[var(--accent-ink)] font-mono">
                        {skill.category}
                      </span>
                    </p>
                    <p className="text-[10px] text-[var(--text-muted)] mt-0.5">
                      {skill.description}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
