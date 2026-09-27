"use client";

import { useEffect } from "react";
import AppErrorState from "@/components/AppErrorState";

/**
 * Root route error boundary (Client Component, per the Next error.js
 * convention). `retry` re-renders the boundary with fresh data; `digest`
 * correlates the failure with server-side logs without leaking the message.
 */
export default function Error({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error("Route render error", error);
  }, [error]);

  return (
    <AppErrorState
      description="An unexpected error occurred while rendering this page. Your data is safe — try again, or head back to start."
      digest={error.digest}
      onRetry={retry}
    />
  );
}