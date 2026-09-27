/**
 * Root route loading state — shown while the first paint of a route segment
 * streams in from the server. Centered Nexus wordmark with the animated
 * acid-lime dots already defined in globals.css.
 */
export default function Loading() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6">
      <div className="flex items-center gap-3">
        <span className="text-2xl font-semibold tracking-tight text-[var(--text-primary)]">
          Nexus
        </span>
        <span className="badge-pill">
          <span className="h-1.5 w-1.5 rounded-full bg-[var(--accent)]" />
          AI
        </span>
      </div>
      <div
        className="flex items-center gap-1.5"
        role="status"
        aria-label="Loading Nexus"
      >
        <span className="stream-dot" />
        <span className="stream-dot" />
        <span className="stream-dot" />
        <span className="sr-only">Loading Nexus…</span>
      </div>
    </div>
  );
}