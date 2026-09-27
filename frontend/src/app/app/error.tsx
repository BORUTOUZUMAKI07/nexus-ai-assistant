"use client";

import { useEffect } from "react";
import AppErrorState from "@/components/AppErrorState";

/**
 * Workspace route error boundary — catches render errors inside /app while
 * the root boundary from app/error.tsx stays available one level up.
 */
export default function Error({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error("Workspace render error", error);
  }, [error]);

  return (
    <AppErrorState
      title="Workspace error"
      description="Something went wrong in the workspace. Your conversations are safe — try again, or reload the page."
      digest={error.digest}
      onRetry={retry}
    />
  );
}