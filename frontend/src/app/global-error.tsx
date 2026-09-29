"use client";

import { useEffect } from "react";

/**
 * Root-level error boundary covering errors thrown in the root layout itself.
 * Next renders this file INSTEAD of the root layout, so the theme tokens from
 * globals.css are not available — everything here is inline styled in the
 * Void / Electric Acid Lime palette, and it brings its own <html>/<body>.
 */
export default function GlobalError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error("Global render error", error);
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: "#fafaf9",
          color: "#16171a",
          fontFamily:
            'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
          letterSpacing: "-0.011em",
        }}
      >
        <div style={{ maxWidth: 420, padding: "2rem", textAlign: "center" }}>
          <div
            style={{
              fontSize: "1.5rem",
              fontWeight: 600,
              letterSpacing: "-0.02em",
            }}
          >
            Nexus <span style={{ color: "#6b7a00" }}>AI</span>
          </div>
          <h1
            style={{
              marginTop: "1.5rem",
              marginBottom: 0,
              fontSize: "1.1rem",
              fontWeight: 600,
            }}
          >
            Something went wrong
          </h1>
          <p
            style={{
              marginTop: "0.75rem",
              fontSize: "0.92rem",
              lineHeight: 1.5,
              color: "#4b4d54",
            }}
          >
            The workspace hit an unexpected error before it could start. Try
            again, and if it keeps happening check the service logs.
          </p>
          {error.digest ? (
            <p
              style={{
                marginTop: "1rem",
                fontSize: "0.8rem",
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                color: "#82858c",
              }}
            >
              digest: {error.digest}
            </p>
          ) : null}
          <button
            type="button"
            onClick={retry}
            style={{
              marginTop: "1.5rem",
              backgroundColor: "#d7e600",
              color: "#16171a",
              fontWeight: 600,
              border: "none",
              borderRadius: 9999,
              padding: "0.65rem 1.75rem",
              fontSize: "0.9rem",
              cursor: "pointer",
              boxShadow: "0 1px 2px rgba(22, 23, 26, 0.14), 0 0 0 1px rgba(107, 122, 0, 0.28)",
            }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}