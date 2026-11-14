const SKELETON_ROWS = [1, 2, 3, 4, 5, 6];

/**
 * Workspace skeleton — the app shell (sidebar + chat composer) while the client
 * bundle hydrates.
 *
 * This used to be `app/app/loading.tsx`, the route-level loading boundary. In
 * production that arrangement never resolved: the request came back as a
 * prerendered shell with the real UI in a `hidden` div waiting for Next's
 * inline reveal script, the reveal never ran, and a hard load of /app sat on
 * the skeleton forever (in-app navigation worked, because it takes the RSC
 * path). Nothing in the console explained it.
 *
 * Rendering the skeleton from the page itself removes that dependency: the page
 * hydrates normally and swaps the skeleton for the workspace on mount, which is
 * also what makes the skeleton honest — it is shown while the app mounts, not
 * while a stream is open.
 */
export default function WorkspaceSkeleton() {
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