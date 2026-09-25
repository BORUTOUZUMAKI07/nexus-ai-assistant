"use client";

import React, { useState } from "react";
import {
  X,
  Copy,
  Check,
  Download,
  Code2,
  Eye,
  Maximize2,
  Minimize2,
  FileCode,
} from "lucide-react";

export interface ArtifactItem {
  id: string;
  title: string;
  language: string;
  content: string;
}

export interface ArtifactCanvasProps {
  artifact: ArtifactItem | null;
  /** Optional list of all artifacts for multi-file tab bar */
  artifacts?: ArtifactItem[];
  onClose: () => void;
  onSelectArtifact?: (artifact: ArtifactItem) => void;
}

export const ArtifactCanvas: React.FC<ArtifactCanvasProps> = ({
  artifact,
  artifacts,
  onClose,
  onSelectArtifact,
}) => {
  const [viewTab, setViewTab] = useState<"code" | "preview">("code");
  const [copied, setCopied] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [showSearch, setShowSearch] = useState(false);
  const searchRef = React.useRef<HTMLInputElement>(null);

  // Keyboard shortcut Ctrl+F / Cmd+F for find in file
  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "f") {
        e.preventDefault();
        setShowSearch(true);
        setTimeout(() => searchRef.current?.focus(), 50);
      }
      if (e.key === "Escape") {
        setShowSearch(false);
        setSearchQuery("");
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Use artifacts array if provided (multi-file mode), or single artifact
  const allArtifacts = artifacts && artifacts.length > 0 ? artifacts : artifact ? [artifact] : [];
  const activeArtifact = artifact;

  if (!activeArtifact) return null;

  const handleCopy = () => {
    navigator.clipboard.writeText(activeArtifact.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownload = () => {
    const extMap: Record<string, string> = {
      python: "py",
      py: "py",
      typescript: "ts",
      javascript: "js",
      tsx: "tsx",
      jsx: "jsx",
      markdown: "md",
      md: "md",
      html: "html",
      css: "css",
      json: "json",
      sql: "sql",
      yaml: "yaml",
      sh: "sh",
      bash: "sh",
    };
    const ext = extMap[activeArtifact.language.toLowerCase()] || "txt";
    const filename = `${activeArtifact.title.replace(/[^a-zA-Z0-9_-]/g, "_")}.${ext}`;
    const blob = new Blob([activeArtifact.content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const lines = activeArtifact.content.split("\n");
  // Highlight lines matching search query
  const lowerSearch = searchQuery.toLowerCase();
  const matchCount = searchQuery
    ? lines.filter((l) => l.toLowerCase().includes(lowerSearch)).length
    : 0;

  return (
    <div
      className={`flex flex-col border-l border-[var(--border-subtle)] bg-[var(--bg-surface)] transition-all duration-200 z-20 ${
        isFullscreen
          ? "fixed inset-0 z-50 bg-[var(--bg-main)]"
          : "w-full md:w-[48%] h-full"
      }`}
    >
      {/* Multi-file tab bar (when multiple artifacts available) */}
      {allArtifacts.length > 1 && (
        <div className="flex overflow-x-auto border-b border-[var(--border-subtle)] bg-[var(--bg-main)] scrollbar-none">
          {allArtifacts.map((art) => (
            <button
              key={art.id}
              onClick={() => onSelectArtifact?.(art)}
              className={`flex items-center gap-1.5 px-3 py-2 text-[11px] font-mono whitespace-nowrap border-r border-[var(--border-subtle)] transition-colors shrink-0 ${
                art.id === activeArtifact.id
                  ? "bg-[var(--bg-surface-elevated)] text-white border-b-2 border-b-[var(--accent)] -mb-px"
                  : "text-[var(--text-muted)] hover:text-white hover:bg-[var(--bg-surface)]"
              }`}
            >
              <FileCode className="w-3 h-3 shrink-0 text-[var(--accent)]" />
              {art.title}
            </button>
          ))}
        </div>
      )}

      {/* Canvas Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border-subtle)] bg-[var(--bg-surface-elevated)]">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-7 h-7 rounded-lg bg-[var(--bg-main)] border border-[var(--border-subtle)] flex items-center justify-center shrink-0">
            <FileCode className="w-3.5 h-3.5 text-[var(--accent)]" />
          </div>
          <div className="min-w-0">
            <h3 className="text-xs font-semibold text-white truncate">
              {activeArtifact.title}
            </h3>
            <span className="text-[10px] uppercase font-mono tracking-wider text-[var(--text-muted)]">
              {activeArtifact.language} • {lines.length} lines
            </span>
          </div>
        </div>

        {/* Header Action Buttons */}
        <div className="flex items-center gap-1.5 shrink-0">
          <div className="flex items-center rounded-lg bg-[var(--bg-main)] p-0.5 border border-[var(--border-subtle)] mr-1">
            <button
              onClick={() => setViewTab("code")}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                viewTab === "code"
                  ? "bg-[var(--bg-surface-elevated)] text-white"
                  : "text-[var(--text-muted)] hover:text-white"
              }`}
            >
              <Code2 className="w-3 h-3" />
              Code
            </button>
            <button
              onClick={() => setViewTab("preview")}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                viewTab === "preview"
                  ? "bg-[var(--bg-surface-elevated)] text-white"
                  : "text-[var(--text-muted)] hover:text-white"
              }`}
            >
              <Eye className="w-3 h-3" />
              Preview
            </button>
          </div>

          <button
            onClick={handleCopy}
            className="p-1.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[var(--text-secondary)] hover:text-white hover:border-[var(--border-strong)] transition-colors"
            title="Copy Code"
          >
            {copied ? (
              <Check className="w-3.5 h-3.5 text-[var(--status-success)]" />
            ) : (
              <Copy className="w-3.5 h-3.5" />
            )}
          </button>

          <button
            onClick={handleDownload}
            className="p-1.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[var(--text-secondary)] hover:text-white hover:border-[var(--border-strong)] transition-colors"
            title="Download File"
          >
            <Download className="w-3.5 h-3.5" />
          </button>

          <button
            onClick={() => setIsFullscreen(!isFullscreen)}
            className="p-1.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[var(--text-secondary)] hover:text-white hover:border-[var(--border-strong)] transition-colors"
            title={isFullscreen ? "Exit Fullscreen" : "Fullscreen"}
          >
            {isFullscreen ? (
              <Minimize2 className="w-3.5 h-3.5" />
            ) : (
              <Maximize2 className="w-3.5 h-3.5" />
            )}
          </button>

          <button
            onClick={onClose}
            className="p-1.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[var(--text-muted)] hover:text-white hover:border-[var(--border-strong)] transition-colors"
            title="Close Canvas"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Find in File bar */}
      {showSearch && (
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[var(--border-subtle)] bg-[var(--bg-surface-elevated)]">
          <input
            ref={searchRef}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Find in file… (Esc to close)"
            className="flex-1 bg-[var(--bg-main)] border border-[var(--border-subtle)] rounded px-2 py-1 text-[11px] text-[var(--text-primary)] outline-none focus:border-[var(--accent)] font-mono placeholder:text-[var(--text-faint)]"
          />
          {searchQuery && (
            <span className="text-[10px] font-mono text-[var(--text-muted)]">
              {matchCount} match{matchCount !== 1 ? "es" : ""}
            </span>
          )}
          <button
            onClick={() => { setShowSearch(false); setSearchQuery(""); }}
            className="p-0.5 text-[var(--text-muted)] hover:text-white transition-colors"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Canvas Body */}
      <div className="flex-1 overflow-auto p-4 font-mono text-xs text-[var(--text-primary)]">
        {viewTab === "code" ? (
          <div className="flex min-w-full">
            {/* Line numbers column */}
            <div className="select-none pr-4 text-right text-[var(--text-faint)] font-mono text-[11px] leading-5 shrink-0 border-r border-[var(--border-subtle)]">
              {lines.map((_, i) => (
                <div key={i}>{i + 1}</div>
              ))}
            </div>
            {/* Code content */}
            <pre className="pl-4 leading-5 overflow-x-auto whitespace-pre font-mono text-[12px] flex-1">
              {lines.map((line, i) => {
                const isMatch = searchQuery && line.toLowerCase().includes(lowerSearch);
                return (
                  <div
                    key={i}
                    className={isMatch ? "bg-[var(--accent-soft)] rounded" : ""}
                  >
                    <code className={isMatch ? "text-[var(--accent)]" : "text-[var(--text-secondary)]"}
                    >{line}</code>
                    {"\n"}
                  </div>
                );
              })}
            </pre>
          </div>
        ) : (
          <div className="prose-nexus text-sm text-[var(--text-primary)] whitespace-pre-wrap font-sans leading-relaxed">
            {activeArtifact.content}
          </div>
        )}
      </div>
    </div>
  );
};
