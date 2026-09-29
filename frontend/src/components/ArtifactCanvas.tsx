"use client";

import React, { useEffect, useState } from "react";
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
  Trash2,
  History,
  CornerUpLeft,
  Pencil,
  Save,
  AlertCircle,
  Loader2,
} from "lucide-react";
import { fetchArtifact, type ArtifactVersionItem } from "@/lib/api";

export interface ArtifactItem {
  id: string;
  title: string;
  language: string;
  content: string;
  /** Server-assigned version when persisted; omitted for transient canvas items */
  version?: number;
  /** True when the artifact is the active, persisted version (mirrors ArtifactDetail) */
  isActiveVersion?: boolean;
}

export interface ArtifactCanvasProps {
  artifact: ArtifactItem | null;
  /** Optional list of all artifacts for multi-file tab bar */
  artifacts?: ArtifactItem[];
  onClose: () => void;
  onSelectArtifact?: (artifact: ArtifactItem) => void;
  /** When provided, shows a Delete button (persisted artifacts only) */
  onDeleteArtifact?: (artifact: ArtifactItem) => void;
  /**
   * Persists a new version of a saved artifact. Required for the Edit control
   * to appear: a transient canvas item has no server-side id, so there is
   * nothing to write a version to.
   */
  onSaveVersion?: (
    artifact: ArtifactItem,
    content: string
  ) => Promise<ArtifactItem | void>;
}

export const ArtifactCanvas: React.FC<ArtifactCanvasProps> = ({
  artifact,
  artifacts,
  onClose,
  onSelectArtifact,
  onDeleteArtifact,
  onSaveVersion,
}) => {
  const [viewTab, setViewTab] = useState<"code" | "preview">("code");
  const [copied, setCopied] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [showSearch, setShowSearch] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // Version history for the active artifact, loaded on demand. null = not
  // loaded / not applicable; [] = loaded and genuinely has no history.
  const [versions, setVersions] = useState<ArtifactVersionItem[] | null>(null);
  // When set, the code view shows this past version instead of the live one.
  const [viewingVersion, setViewingVersion] = useState<ArtifactVersionItem | null>(null);
  // Edit buffer. `isEditing` gates the textarea; `draft` holds the unsaved text
  // so toggling Edit and back does not lose work. `saved` drives the transient
  // "version N saved" confirmation.
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedVersion, setSavedVersion] = useState<number | null>(null);
  const searchRef = React.useRef<HTMLInputElement>(null);
  // The Ctrl/Cmd+S shortcut reads both of these through refs rather than
  // closing over them. Binding the listener with [isEditing] in its dependency
  // array left it holding a stale `isEditing`, so the shortcut silently did
  // nothing while an edit was open -- the one state it exists for.
  const handleSaveRef = React.useRef<() => Promise<void>>(async () => {});
  const isEditingRef = React.useRef(false);

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
        // Escape abandons an edit. Handled here rather than on the textarea so
        // it works whether or not the field has focus.
        setIsEditing(false);
        setSaveError(null);
      }
      // Ctrl/Cmd+S saves an in-progress edit instead of the browser's own
      // "save page" dialog, which would lose the buffer.
      if (
        (e.ctrlKey || e.metaKey) &&
        e.key === "s" &&
        isEditingRef.current
      ) {
        e.preventDefault();
        void handleSaveRef.current();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // Registered once: the handler reads current state through refs, so
    // re-binding per render would only churn the listener.
  }, []);

  // Use artifacts array if provided (multi-file mode), or single artifact
  const allArtifacts = artifacts && artifacts.length > 0 ? artifacts : artifact ? [artifact] : [];
  const activeArtifact = artifact;
  // Identity of the open artifact, for detecting a switch. Keyed on the fields
  // that make two rows genuinely different documents -- the content itself is
  // deliberately excluded, since it changes as the user types.
  const artifactKey = activeArtifact
    ? `${activeArtifact.id ?? "transient"}:${activeArtifact.version ?? "new"}`
    : "none";

  // Reset per-artifact state when the open artifact changes.
  //
  // Done during render rather than in an effect: an effect runs *after* the
  // canvas has already painted the new artifact holding the previous one's
  // draft and version list, which shows as a flash of the wrong content. React
  // re-renders immediately without committing when state is adjusted this way,
  // so the user only ever sees the new artifact's own state.
  //
  // Switching artifacts must abandon any unsaved edit, or the next artifact
  // would open showing the previous one's draft.
  const [renderedArtifactKey, setRenderedArtifactKey] = useState(artifactKey);
  if (artifactKey !== renderedArtifactKey) {
    setRenderedArtifactKey(artifactKey);
    setViewingVersion(null);
    setVersions(null);
    setIsEditing(false);
    setDraft("");
    setSaveError(null);
    setSavedVersion(null);
  }

  // Load version history for the open artifact. Only persisted artifacts have
  // history; a transient canvas item has no id on the server, so requesting it
  // would just 404.
  // `activeArtifact` itself is a fresh object literal on every parent render, so
  // it cannot be a dependency -- the artifact's identity is carried by
  // `artifactKey`, which changes only when the open document really changes.
  useEffect(() => {
    if (!activeArtifact || activeArtifact.version === undefined) return;

    let cancelled = false;
    // The spinner is driven off `versions` being unset rather than a separate
    // boolean set here: a setState before the fetch would be a synchronous
    // state write in an effect, and the render-time reset above already leaves
    // `versions` null for a freshly opened artifact.
    fetchArtifact(activeArtifact.id)
      .then((detail) => {
        if (cancelled) return;
        setVersions(Array.isArray(detail.versions) ? detail.versions : []);
      })
      .catch(() => {
        // Version history is supplementary — failing to load it must not break
        // the canvas itself.
        if (!cancelled) setVersions([]);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see the note above
  }, [artifactKey]);

  // The content actually shown: a selected past version, or the live one.
  const displayContent = viewingVersion
    ? viewingVersion.content
    : (activeArtifact?.content ?? "");
  const displayTitle = viewingVersion
    ? `${activeArtifact?.title} (v${viewingVersion.version})`
    : activeArtifact?.title;

  /** Only a persisted artifact can take a new version, and only if a saver exists. */
  const canEdit = Boolean(onSaveVersion) && activeArtifact?.version !== undefined;

  const handleSave = async () => {
    if (!activeArtifact || !canEdit || !onSaveVersion || saving) return;
    const next = draft;
    // Saving identical text would burn a version number and a history row for
    // no change. The backend snapshots the old content, so this is not free.
    if (next === activeArtifact.content) {
      setSaveError("No changes to save.");
      return;
    }

    setSaving(true);
    setSaveError(null);
    try {
      const updated = await onSaveVersion(activeArtifact, next);
      setIsEditing(false);
      setDraft("");
      setSavedVersion(updated?.version ?? (activeArtifact.version ?? 0) + 1);
      setTimeout(() => setSavedVersion(null), 4000);
    } catch (err) {
      // Stay in edit mode with the buffer intact so the work is not lost.
      setSaveError(
        err instanceof Error ? err.message : "Could not save a new version"
      );
    } finally {
      setSaving(false);
    }
  };

  // Publish the current edit state to the once-bound keydown listener. Kept in
  // an effect (rather than assigned during render) so the refs are only written
  // once the render is committed.
  //
  // This hook has to sit above the `!activeArtifact` early return below: the
  // canvas is mounted with no artifact and handed one a render later, and a
  // hook that only ran on the second of those renders would change the hook
  // count between them.
  React.useEffect(() => {
    isEditingRef.current = isEditing;
    handleSaveRef.current = handleSave;
  });

  if (!activeArtifact) return null;

  const handleCopy = () => {
    navigator.clipboard.writeText(displayContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const beginEdit = () => {
    // Seed the buffer from the live content, never from a past version: saving
    // "v2" must not silently overwrite the current file with v1's text.
    setDraft(activeArtifact.content);
    setSaveError(null);
    setIsEditing(true);
  };

  const cancelEdit = () => {
    setIsEditing(false);
    setDraft("");
    setSaveError(null);
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
    // Downloads what is on screen, so downloading while previewing an old
    // version gives you that version rather than silently the latest.
    const blob = new Blob([displayContent], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  // While editing, the buffer is what the line numbers and match count describe
  // -- otherwise the gutter would be out of step with the visible text.
  const lines = (isEditing ? draft : displayContent).split("\n");
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
                  ? "bg-[var(--bg-surface-elevated)] text-[var(--text-primary)] border-b-2 border-b-[var(--accent)] -mb-px"
                  : "text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-surface)]"
              }`}
            >
              <FileCode className="w-3 h-3 shrink-0 text-[var(--accent-ink)]" />
              {art.title}
            </button>
          ))}
        </div>
      )}

      {/* Canvas Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border-subtle)] bg-[var(--bg-surface-elevated)]">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-7 h-7 rounded-lg bg-[var(--bg-main)] border border-[var(--border-subtle)] flex items-center justify-center shrink-0">
            <FileCode className="w-3.5 h-3.5 text-[var(--accent-ink)]" />
          </div>
          <div className="min-w-0">
            <h3 className="text-xs font-semibold text-[var(--text-primary)] truncate">
              {displayTitle}
            </h3>
            <span className="text-[10px] uppercase font-mono tracking-wider text-[var(--text-muted)] flex items-center gap-1.5">
              {activeArtifact.language} • {lines.length} lines
              {activeArtifact.version !== undefined && (
                <span
                  className={`px-1.5 py-0.5 rounded text-[9px] font-semibold border ${
                    activeArtifact.isActiveVersion
                      ? "bg-[var(--accent-soft)] text-[var(--accent-ink)] border-[var(--accent)]/40"
                      : "bg-[var(--bg-main)] text-[var(--text-muted)] border-[var(--border-subtle)]"
                  }`}
                  title={
                    activeArtifact.isActiveVersion
                      ? `Persisted version ${activeArtifact.version}`
                      : `Snapshot of version ${activeArtifact.version}`
                  }
                >
                  v{activeArtifact.version}
                  {activeArtifact.isActiveVersion ? " • saved" : ""}
                </span>
              )}
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
                  ? "bg-[var(--bg-surface-elevated)] text-[var(--text-primary)]"
                  : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              }`}
            >
              <Code2 className="w-3 h-3" />
              Code
            </button>
            <button
              onClick={() => setViewTab("preview")}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                viewTab === "preview"
                  ? "bg-[var(--bg-surface-elevated)] text-[var(--text-primary)]"
                  : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              }`}
            >
              <Eye className="w-3 h-3" />
              Preview
            </button>
          </div>

          <button
            onClick={handleCopy}
            className="p-1.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--border-strong)] transition-colors"
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
            className="p-1.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--border-strong)] transition-colors"
            title="Download File"
          >
            <Download className="w-3.5 h-3.5" />
          </button>

          <button
            onClick={() => setIsFullscreen(!isFullscreen)}
            className="p-1.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--border-strong)] transition-colors"
            title={isFullscreen ? "Exit Fullscreen" : "Fullscreen"}
          >
            {isFullscreen ? (
              <Minimize2 className="w-3.5 h-3.5" />
            ) : (
              <Maximize2 className="w-3.5 h-3.5" />
            )}
          </button>

          {canEdit && !isEditing && (
            <button
              onClick={beginEdit}
              disabled={viewingVersion !== null}
              className="p-1.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[var(--text-secondary)] hover:text-white hover:border-[var(--border-strong)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              // Stable accessible name; only the tooltip varies with state, so
              // the control does not appear to vanish while a past version is
              // being previewed.
              aria-label="Edit and save a new version"
              title={
                viewingVersion
                  ? "Back to current before editing"
                  : "Edit and save a new version"
              }
            >
              <Pencil className="w-3.5 h-3.5" />
            </button>
          )}

          {onDeleteArtifact && activeArtifact.version !== undefined && (
            <button
              onClick={() => {
                setDeleting(true);
                onDeleteArtifact(activeArtifact);
              }}
              disabled={deleting}
              className="p-1.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[var(--text-muted)] hover:text-[var(--status-danger)] hover:border-[var(--status-danger)]/50 transition-colors disabled:opacity-50"
              title="Delete artifact and all versions"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}

          <button
            onClick={onClose}
            className="p-1.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:border-[var(--border-strong)] transition-colors"
            title="Close Canvas"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Editor toolbar. Save/Cancel live here rather than in the body so the
          buffer stays put while the body swaps between code and preview.
          The error belongs here too: a failed save keeps the editor open, so
          reporting it only after the editor closes would hide it entirely. */}
      {isEditing && (
        <div className="px-3 py-1.5 border-b border-[var(--accent)]/30 bg-[var(--accent-soft)] text-[11px] text-[var(--accent)]">
          {saveError ? (
            <p className="flex items-center gap-1.5 text-[var(--status-danger)]">
              <AlertCircle className="w-3 h-3 shrink-0" />
              {saveError}
            </p>
          ) : (
            <div className="flex items-center justify-between gap-2">
              <span className="truncate">
                Editing — saving writes version{" "}
                {(activeArtifact.version ?? 0) + 1}; the current version is kept
                as history.
              </span>
              <div className="flex items-center gap-1.5 shrink-0">
                <button
                  onClick={cancelEdit}
                  disabled={saving}
                  className="px-2 py-0.5 rounded border border-[var(--border-subtle)] hover:bg-[var(--bg-main)] transition-colors disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  onClick={() => void handleSave()}
                  disabled={saving}
                  className="flex items-center gap-1 px-2 py-0.5 rounded bg-[var(--accent)] text-[var(--accent-foreground)] font-semibold hover:bg-[var(--accent-hover)] transition-colors disabled:opacity-50"
                >
                  {saving ? (
                    <Loader2 className="w-3 h-3 animate-spin" />
                  ) : (
                    <Save className="w-3 h-3" />
                  )}
                  Save new version
                </button>
              </div>
            </div>
          )}
          {/* With an error on screen the buttons are still needed, otherwise
              the only way out is Escape. */}
          {saveError && (
            <div className="flex items-center justify-end gap-1.5 mt-1.5">
              <button
                onClick={cancelEdit}
                disabled={saving}
                className="px-2 py-0.5 rounded border border-[var(--border-subtle)] hover:bg-[var(--bg-main)] transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={() => void handleSave()}
                disabled={saving}
                className="flex items-center gap-1 px-2 py-0.5 rounded bg-[var(--accent)] text-[var(--accent-foreground)] font-semibold hover:bg-[var(--accent-hover)] transition-colors disabled:opacity-50"
              >
                {saving ? (
                  <Loader2 className="w-3 h-3 animate-spin" />
                ) : (
                  <Save className="w-3 h-3" />
                )}
                Save new version
              </button>
            </div>
          )}
        </div>
      )}

      {saveError && !isEditing && (
        <p className="px-3 py-1.5 border-b border-[var(--status-danger)]/30 bg-[var(--status-danger)]/10 text-[11px] text-[var(--status-danger)] flex items-center gap-1.5">
          <AlertCircle className="w-3 h-3" />
          {saveError}
        </p>
      )}

      {savedVersion !== null && !isEditing && (
        <p className="px-3 py-1.5 border-b border-[var(--status-success)]/30 bg-[var(--status-success)]/10 text-[11px] text-[var(--status-success)] flex items-center gap-1.5">
          <Check className="w-3 h-3" /> Saved as version {savedVersion}
        </p>
      )}

      {/* Return-to-current banner while previewing an earlier version */}
      {viewingVersion && (
        <div className="flex items-center justify-between gap-2 px-3 py-1.5 border-b border-[var(--accent)]/30 bg-[var(--accent-soft)] text-[11px] text-[var(--accent)]">
          <span>
            Viewing version {viewingVersion.version} — this is not the current version.
          </span>
          <button
            onClick={() => setViewingVersion(null)}
            className="flex items-center gap-1 px-1.5 py-0.5 rounded border border-[var(--accent)]/40 hover:bg-[var(--accent)]/10 transition-colors"
          >
            <CornerUpLeft className="w-3 h-3" />
            Back to current
          </button>
        </div>
      )}

      {/* Version history. The backend stored every version and the header already
          reported the current number, but nothing ever listed them, so the history
          was unreachable from the UI. */}
      {activeArtifact.version !== undefined && !viewingVersion && !isEditing && (
        <details className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)]">
          <summary className="flex cursor-pointer items-center gap-1.5 px-3 py-1.5 text-[10px] uppercase font-mono tracking-wider text-[var(--text-muted)] select-none hover:text-[var(--text-secondary)]">
            <History className="w-3 h-3" />
            <span>Version history</span>
            {versions === null && (
              <span className="normal-case tracking-normal">loading…</span>
            )}
            {versions !== null && (
              <span className="normal-case tracking-normal">({versions.length})</span>
            )}
          </summary>

          {versions !== null && versions.length > 0 && (
            <ul className="max-h-40 overflow-y-auto border-t border-[var(--border-subtle)]">
              {versions
                .slice()
                .sort((a, b) => b.version - a.version)
                .map((v) => (
                  <li
                    key={v.id}
                    className="flex items-center justify-between gap-2 px-3 py-1.5 border-b border-[var(--border-subtle)] last:border-b-0 text-[11px]"
                  >
                    <span className="min-w-0 flex-1">
                      <span
                        className={`font-mono font-semibold ${
                          v.version === activeArtifact.version
                            ? "text-[var(--accent)]"
                            : "text-[var(--text-secondary)]"
                        }`}
                      >
                        v{v.version}
                      </span>
                      <span className="ml-2 text-[var(--text-faint)]">
                        {new Date(v.created_at).toLocaleString(undefined, {
                          dateStyle: "medium",
                          timeStyle: "short",
                        })}
                      </span>
                      {v.title && v.title !== activeArtifact.title && (
                        <span className="ml-2 text-[var(--text-faint)] truncate">
                          &quot;{v.title}&quot;
                        </span>
                      )}
                    </span>
                    {v.version !== activeArtifact.version && (
                      <button
                        onClick={() => setViewingVersion(v)}
                        title={`View version ${v.version}`}
                        aria-label={`View version ${v.version}`}
                        className="shrink-0 flex items-center gap-1 px-1.5 py-0.5 rounded border border-[var(--border-subtle)] text-[var(--text-muted)] hover:text-white hover:border-[var(--border-strong)] transition-colors"
                      >
                        <Eye className="w-3 h-3" />
                        View
                      </button>
                    )}
                  </li>
                ))}
            </ul>
          )}

          {versions !== null && versions.length === 0 && (
            <p className="px-3 py-2 border-t border-[var(--border-subtle)] text-[11px] text-[var(--text-faint)]">
              No earlier versions recorded.
            </p>
          )}
        </details>
      )}

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
            className="p-0.5 text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Canvas Body */}
      <div className="flex-1 overflow-auto p-4 font-mono text-xs text-[var(--text-primary)]">
        {isEditing ? (
          <div className="flex min-w-full">
            {/* Same gutter as the read-only view, counting the draft rather
                than the saved file, so the numbers track what is on screen. */}
            <div
              aria-hidden="true"
              className="select-none pr-4 text-right text-[var(--text-faint)] font-mono text-[11px] leading-5 shrink-0 border-r border-[var(--border-subtle)]"
            >
              {lines.map((_, i) => (
                <div key={i}>{i + 1}</div>
              ))}
            </div>
            <textarea
              // Autofocus is deliberate: the textarea only exists once the user
              // has asked to edit, so there is nothing for it to steal focus
              // from.
              autoFocus
              aria-label={`Edit ${activeArtifact.title}`}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              spellCheck={false}
              className="pl-4 h-full min-h-[24rem] flex-1 resize-none bg-transparent outline-none text-[12px] leading-5 text-[var(--text-primary)] font-mono"
            />
          </div>
        ) : viewTab === "code" ? (
          <div className="flex min-w-full">
            {/* Line numbers column */}
            <div
              aria-hidden="true"
              className="select-none pr-4 text-right text-[var(--text-faint)] font-mono text-[11px] leading-5 shrink-0 border-r border-[var(--border-subtle)]"
            >
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
                    <code className={isMatch ? "text-[var(--accent-ink)]" : "text-[var(--text-secondary)]"}
                    >{line}</code>
                    {"\n"}
                  </div>
                );
              })}
            </pre>
          </div>
        ) : (
          <div className="prose-nexus text-sm text-[var(--text-primary)] whitespace-pre-wrap font-sans leading-relaxed">
            {displayContent}
          </div>
        )}
      </div>
    </div>
  );
};
