"use client";

import React, { useState, useRef, useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Upload,
  FileText,
  Trash2,
  Search,
  CheckCircle2,
  Database,
  Loader2,
  AlertCircle,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  X,
} from "lucide-react";
import {
  uploadFile,
  deleteFile,
  ragQuery,
  KnowledgeFile,
  MessageCitation,
} from "@/lib/api";
import { useKnowledgeFiles, knowledgeKeys } from "@/lib/queries";

// A search hit is a citation. The backend returns the same shape the chat
// transcript renders, so it is typed as MessageCitation rather than a local
// near-duplicate that would silently drift from the real fields.

export const KnowledgeView: React.FC = () => {
  const queryClient = useQueryClient();
  // Server state on the query client. The poll that kept pending uploads fresh
  // is now a `refetchInterval` predicate on the query (see `useKnowledgeFiles`),
  // so it stops when every file reaches a terminal state instead of relying on a
  // timer that could outlive the component.
  const filesQuery = useKnowledgeFiles();
  const files = useMemo(() => filesQuery.data ?? [], [filesQuery.data]);
  // `isPending` is "no data yet", so the list skeleton shows on first load only;
  // a background poll does not blank the table it is refreshing.
  const isLoading = filesQuery.isPending;
  const loadError = filesQuery.error
    ? filesQuery.error instanceof Error
      ? filesQuery.error.message
      : "Could not load documents"
    : null;
  const [actionError, setActionError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<MessageCitation[]>([]);
  const [searchTookMs, setSearchTookMs] = useState<number | null>(null);
  // The last query actually sent, used to tell "searched and found nothing"
  // apart from "has not searched yet". Results are not cleared when the box is
  // edited, so the empty state must not key off the live input.
  const [searchedQuery, setSearchedQuery] = useState<string | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [docFilter, setDocFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "indexed" | "processing" | "failed">("all");
  const [docSort, setDocSort] = useState<"newest" | "oldest" | "name" | "size">("newest");
  const [docPage, setDocPage] = useState(1);
  const DOCS_PER_PAGE = 6;
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Kept as a named wrapper so the Retry button's `onClick={load}` is unchanged;
  // `refetch` returns a promise the click handler does not await.
  const load = () => {
    void filesQuery.refetch();
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0) return;
    const file = e.target.files[0];
    setIsUploading(true);
    setActionError(null);
    try {
      const newFile = await uploadFile(file);
      // Prepend to the cache rather than refetching: the just-uploaded file is
      // authoritative from this response, and a refetch would briefly show the
      // server's list without it (the row appears after indexing begins).
      queryClient.setQueryData<KnowledgeFile[]>(knowledgeKeys.all, (prev) => [
        newFile,
        ...(prev ?? []),
      ]);
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Upload failed"
      );
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleDeleteFile = async (id: string) => {
    setActionError(null);
    try {
      await deleteFile(id);
      queryClient.setQueryData<KnowledgeFile[]>(knowledgeKeys.all, (prev) =>
        (prev ?? []).filter((f) => f.id !== id),
      );
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Delete failed"
      );
    }
  };

  const filteredAndSortedFiles = React.useMemo(() => {
    let result = files;
    if (docFilter.trim()) {
      const q = docFilter.trim().toLowerCase();
      result = result.filter(
        (f) =>
          (f.original_filename || f.filename).toLowerCase().includes(q)
      );
    }
    if (statusFilter !== "all") {
      result = result.filter((f) => f.status === statusFilter);
    }
    return [...result].sort((a, b) => {
      if (docSort === "oldest") {
        return (a.created_at ?? "").localeCompare(b.created_at ?? "");
      }
      if (docSort === "name") {
        return (a.original_filename || a.filename).localeCompare(
          b.original_filename || b.filename
        );
      }
      if (docSort === "size") {
        return b.size_bytes - a.size_bytes;
      }
      // newest first (default)
      return (b.created_at ?? "").localeCompare(a.created_at ?? "");
    });
  }, [files, docFilter, statusFilter, docSort]);

  const totalDocPages = Math.max(1, Math.ceil(filteredAndSortedFiles.length / DOCS_PER_PAGE));
  const paginatedFiles = filteredAndSortedFiles.slice(
    (docPage - 1) * DOCS_PER_PAGE,
    docPage * DOCS_PER_PAGE
  );

  const handleSearch = async () => {
    const query = searchQuery.trim();
    if (!query) return;
    setIsSearching(true);
    setSearchError(null);
    setSearchResults([]);
    setSearchTookMs(null);
    setSearchedQuery(query);
    try {
      // Goes through the api module rather than a hand-rolled fetch, so the
      // auth headers and the shared error wording come from one place.
      const data = await ragQuery(query);
      setSearchResults(data.citations ?? []);
      setSearchTookMs(typeof data.took_ms === "number" ? data.took_ms : null);
    } catch (err) {
      setSearchError(
        err instanceof Error ? err.message : "Search failed"
      );
    } finally {
      setIsSearching(false);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto p-8 max-w-4xl mx-auto space-y-8">
      {/* Header */}
      <div>
        <h2 className="text-xl font-semibold tracking-tight text-[var(--text-primary)] flex items-center gap-2">
          <Database className="w-5 h-5 text-[var(--accent-ink)]" /> Knowledge base
        </h2>
        <p className="text-xs text-[var(--text-muted)] mt-1">
          Documents indexed for semantic hybrid search.
        </p>
      </div>

      {/* Upload Box */}
      <div className="rounded-xl border border-dashed border-[var(--border-subtle)] hover:border-[var(--border-strong)] p-8 text-center transition-colors bg-[var(--bg-surface)]">
        <input
          type="file"
          ref={fileInputRef}
          onChange={handleFileUpload}
          className="hidden"
          accept=".pdf,.md,.txt,.json,.csv,.py,.js,.ts"
        />
        <Upload className="w-8 h-8 text-[var(--text-muted)] mx-auto mb-3" />
        <h3 className="text-sm font-medium text-[var(--text-primary)] mb-1">
          Upload a document
        </h3>
        <p className="text-xs text-[var(--text-muted)] mb-4">
          PDF, Markdown, TXT, JSON, or source code
        </p>
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={isUploading}
          className="px-4 py-2 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] disabled:opacity-50 text-[var(--accent-foreground)] text-xs font-semibold transition-all inline-flex items-center gap-2 shadow-sm"
        >
          {isUploading ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>Indexing document…</span>
            </>
          ) : (
            <span>Browse documents</span>
          )}
        </button>
      </div>

      {actionError && (
        <div className="flex items-center gap-3 rounded-xl border border-[var(--border-strong)] bg-[var(--bg-surface)] p-4 text-sm">
          <AlertCircle className="w-4 h-4 text-[var(--status-danger)] shrink-0" />
          <span className="text-[var(--text-secondary)]">{actionError}</span>
        </div>
      )}

      {/* Indexed Files */}
      <div className="space-y-3">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <h3 className="text-sm font-medium text-[var(--text-secondary)]">
            Indexed documents {files.length > 0 && `(${files.length})`}
          </h3>

          {/* Filtering & Sorting Controls */}
          {files.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              {/* Search file filter */}
              <div className="relative">
                <Search className="w-3.5 h-3.5 absolute left-2.5 top-2 text-[var(--text-faint)]" />
                <input
                  type="text"
                  value={docFilter}
                  onChange={(e) => {
                    setDocFilter(e.target.value);
                    setDocPage(1);
                  }}
                  placeholder="Filter documents…"
                  className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg pl-8 pr-6 py-1 text-xs text-[var(--text-primary)] placeholder-[var(--text-faint)] focus:outline-none focus:border-[var(--accent)] w-36 sm:w-44"
                />
                {docFilter && (
                  <button
                    onClick={() => { setDocFilter(""); setDocPage(1); }}
                    className="absolute right-2 top-1.5 text-[var(--text-faint)] hover:text-[var(--text-primary)]"
                  >
                    <X className="w-3 h-3" />
                  </button>
                )}
              </div>

              {/* Status Filter */}
              <select
                value={statusFilter}
                onChange={(e) => {
                  setStatusFilter(e.target.value as "all" | "indexed" | "processing" | "failed");
                  setDocPage(1);
                }}
                className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg px-2 py-1 text-xs text-[var(--text-secondary)] focus:outline-none"
              >
                <option value="all">All statuses</option>
                <option value="indexed">Indexed</option>
                <option value="processing">Processing</option>
                <option value="failed">Failed</option>
              </select>

              {/* Sort Order */}
              <select
                value={docSort}
                onChange={(e) => {
                  setDocSort(e.target.value as "newest" | "oldest" | "name" | "size");
                  setDocPage(1);
                }}
                className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg px-2 py-1 text-xs text-[var(--text-secondary)] focus:outline-none"
              >
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first</option>
                <option value="name">Name (A-Z)</option>
                <option value="size">Size (largest)</option>
              </select>
            </div>
          )}
        </div>

        {isLoading ? (
          <div className="glass-panel p-6 text-center text-xs text-[var(--text-muted)]">
            Loading documents…
          </div>
        ) : loadError ? (
          <div className="glass-panel p-6">
            <div className="flex items-center justify-center gap-3 text-xs text-[var(--text-muted)]">
              <AlertCircle className="w-4 h-4 text-[var(--status-danger)]" />
              <span>{loadError}</span>
              <button
                onClick={load}
                className="flex items-center gap-1.5 rounded-lg border border-[var(--border-subtle)] px-2.5 py-1 text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
              >
                <RefreshCw className="w-3 h-3" />
                Retry
              </button>
            </div>
          </div>
        ) : files.length === 0 ? (
          <div className="glass-panel p-6 text-center text-xs text-[var(--text-muted)]">
            No documents indexed yet. Upload one above.
          </div>
        ) : filteredAndSortedFiles.length === 0 ? (
          <div className="glass-panel p-6 text-center text-xs text-[var(--text-muted)]">
            No documents match the current filter.
          </div>
        ) : (
          <div className="glass-panel overflow-hidden">
            <div className="divide-y divide-[var(--border-subtle)]">
              {paginatedFiles.map((f) => (
                <div
                  key={f.id}
                  className="p-4 flex items-center justify-between text-xs hover:bg-[var(--bg-main)] transition-colors"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <FileText className="w-4 h-4 text-[var(--text-muted)] shrink-0" />
                    <div>
                      <span className="font-medium text-[var(--text-secondary)] block truncate max-w-sm">
                        {f.original_filename || f.filename}
                      </span>
                      <span className="text-[10px] text-[var(--text-faint)]">
                        {(f.size_bytes / 1024).toFixed(1)} KB • {f.chunk_count} chunks
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    {f.status === "pending" || f.status === "processing" ? (
                      <span
                        className="flex items-center gap-1 text-[var(--accent-ink)] text-[10px] bg-[var(--bg-main)] px-2 py-0.5 rounded-md font-mono border border-[var(--border-subtle)]"
                        title="Document is still being indexed in the background"
                      >
                        <Loader2 className="w-3 h-3 animate-spin" /> Indexing…
                      </span>
                    ) : f.status === "failed" ? (
                      <span
                        className="flex items-center gap-1 text-[var(--status-danger)] text-[10px] bg-[var(--bg-main)] px-2 py-0.5 rounded-md font-mono border border-[var(--border-subtle)]"
                        title={f.error_message ?? "Indexing failed"}
                      >
                        <AlertCircle className="w-3 h-3" /> Failed
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-[var(--status-success)] text-[10px] bg-[var(--bg-main)] px-2 py-0.5 rounded-md font-mono border border-[var(--border-subtle)]">
                        <CheckCircle2 className="w-3 h-3" /> Indexed
                      </span>
                    )}
                    <button
                      onClick={() => handleDeleteFile(f.id)}
                      className="text-[var(--text-faint)] hover:text-[var(--status-danger)] p-1 transition-colors"
                      title="Delete document"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>

            {/* Pagination Controls */}
            {totalDocPages > 1 && (
              <div className="p-3 border-t border-[var(--border-subtle)] bg-[var(--bg-main)] flex items-center justify-between text-xs text-[var(--text-muted)]">
                <span>
                  Showing {Math.min((docPage - 1) * DOCS_PER_PAGE + 1, filteredAndSortedFiles.length)}–
                  {Math.min(docPage * DOCS_PER_PAGE, filteredAndSortedFiles.length)} of {filteredAndSortedFiles.length}
                </span>
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => setDocPage((p) => Math.max(1, p - 1))}
                    disabled={docPage === 1}
                    className="p-1 rounded-md border border-[var(--border-subtle)] hover:bg-[var(--bg-surface)] disabled:opacity-40 transition-colors"
                    title="Previous page"
                  >
                    <ChevronLeft className="w-3.5 h-3.5" />
                  </button>
                  <span className="px-2 text-[11px] font-mono">
                    Page {docPage} of {totalDocPages}
                  </span>
                  <button
                    onClick={() => setDocPage((p) => Math.min(totalDocPages, p + 1))}
                    disabled={docPage === totalDocPages}
                    className="p-1 rounded-md border border-[var(--border-subtle)] hover:bg-[var(--bg-surface)] disabled:opacity-40 transition-colors"
                    title="Next page"
                  >
                    <ChevronRight className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Hybrid Search */}
      <div className="space-y-3">
        <h3 className="text-sm font-medium text-[var(--text-secondary)]">
          Semantic search
        </h3>
        <div className="flex gap-2">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            placeholder="Ask a question about your documents…"
            className="flex-1 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3.5 py-2 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent)] transition-colors placeholder:text-[var(--text-faint)]"
          />
          <button
            onClick={handleSearch}
            disabled={isSearching || !searchQuery.trim()}
            className="px-4 py-2 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] disabled:opacity-50 text-[var(--accent-foreground)] text-xs font-semibold flex items-center gap-1.5 transition-all shadow-sm"
          >
            {isSearching ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Search className="w-3.5 h-3.5" />
            )}
            <span>Search</span>
          </button>
        </div>

        {searchError && (
          <p className="text-xs text-[var(--status-danger)]">{searchError}</p>
        )}

        {/* A search that matched nothing is a real answer, not an empty state
            to hide -- otherwise the user cannot tell "nothing indexed this"
            apart from "the request never ran". */}
        {searchResults.length === 0 &&
          !isSearching &&
          !searchError &&
          searchedQuery !== null && (
          <div className="glass-panel p-6 text-center text-xs text-[var(--text-muted)]">
            No matching passages in your indexed documents.
          </div>
        )}

        {searchResults.length > 0 && (
          <div className="space-y-2 mt-1">
            <p className="text-[10px] text-[var(--text-faint)] font-mono">
              {searchResults.length} passage{searchResults.length === 1 ? "" : "s"}
              {searchTookMs !== null ? ` in ${searchTookMs}ms` : ""}
            </p>
            {searchResults.map((r, idx) => (
              <div
                key={`${r.filename ?? "source"}-${r.chunk_index ?? idx}`}
                className="p-3.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] text-xs space-y-1"
              >
                <div className="flex items-center justify-between text-[11px] text-[var(--accent-ink)] font-mono">
                  <span>
                    {r.filename ?? "source"} (Chunk {r.chunk_index ?? "?"})
                  </span>
                  {/* A missing score is rendered as such rather than invented:
                      showing "0.9+" would read as a real measurement. */}
                  {typeof r.score === "number" ? (
                    <span className="px-1.5 py-0.5 rounded bg-[var(--bg-main)] border border-[var(--border-subtle)] text-[var(--text-muted)]">
                      Score: {r.score.toFixed(3)}
                    </span>
                  ) : (
                    <span
                      className="px-1.5 py-0.5 rounded bg-[var(--bg-main)] border border-[var(--border-subtle)] text-[var(--text-faint)]"
                      title="The backend did not return a relevance score for this passage"
                    >
                      Score: n/a
                    </span>
                  )}
                </div>
                <p className="text-[var(--text-secondary)] leading-relaxed text-[11px]">
                  {r.content_snippet || r.snippet || (
                    <span className="italic text-[var(--text-faint)]">
                      This passage had no text preview.
                    </span>
                  )}
                </p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};