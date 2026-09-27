/**
 * Sign-in route loading state — compact branded loader that keeps the auth
 * card feel without waiting on the full page bundle.
 */
export default function Loading() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <div
        className="flex flex-col items-center gap-5"
        role="status"
        aria-label="Loading Sign in"
      >
        <span className="text-xl font-semibold tracking-tight text-[var(--text-primary)]">
          Nexus
        </span>
        <div className="flex items-center gap-1.5">
          <span className="stream-dot" />
          <span className="stream-dot" />
          <span className="stream-dot" />
          <span className="sr-only">Loading Sign in…</span>
        </div>
      </div>
    </div>
  );
}