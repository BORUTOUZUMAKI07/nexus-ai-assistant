const SKELETON_ROWS = [1, 2, 3, 4, 5, 6];

/**
 * Workspace loading state — a skeleton of the app shell (sidebar + chat
 * composer) so navigation feels instant while the client bundle hydrates.
 */
export default function Loading() {
  return (
    <div
      className="flex h-screen animate-pulse"
      role="status"
      aria-label="Loading workspace"
    >
      <aside className="w-72 shrink-0 border-r border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
        <div className="h-8 w-24 rounded-md bg-[var(--border-subtle)]" />
        {SKELETON_ROWS.map((row) => (
          <div
            key={row}
            className="mt-3 h-10 rounded-lg bg-[var(--bg-surface-elevated)]"
          />
        ))}
      </aside>
      <main className="flex flex-1 flex-col bg-[var(--bg-main)]">
        <div className="flex flex-1 items-center justify-center">
          <div className="flex items-center gap-1.5">
            <span className="stream-dot" />
            <span className="stream-dot" />
            <span className="stream-dot" />
          </div>
        </div>
        <div className="border-t border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
          <div className="mx-auto h-12 max-w-3xl rounded-full bg-[var(--bg-surface-elevated)]" />
        </div>
      </main>
      <span className="sr-only">Loading workspace…</span>
    </div>
  );
}