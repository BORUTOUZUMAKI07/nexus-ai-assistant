import Link from "next/link";

/**
 * Branded 404 page — rendered inside the root layout, so theme tokens and
 * utility classes from globals.css apply. The giant gradient number echoes
 * the landing hero's Electric Acid Lime branding.
 */
export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-8 px-6">
      <p
        className="text-gradient text-8xl font-bold tracking-tighter"
        aria-hidden="true"
      >
        404
      </p>
      <div className="glass-card w-full max-w-md px-8 py-6 text-center">
        <h1 className="text-lg font-semibold text-[var(--text-primary)]">
          Page not found
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted)]">
          The page you are looking for does not exist or has moved. Check the
          address and try again.
        </p>
      </div>
      <Link href="/" className="btn-electric px-5 py-2 text-sm">
        Back to start
      </Link>
    </div>
  );
}