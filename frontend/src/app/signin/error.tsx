"use client";

import { useEffect } from "react";
import AppErrorState from "@/components/AppErrorState";

/**
 * Sign-in route error boundary — same branded fallback, tuned for the auth
 * flow.
 */
export default function Error({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error("Sign-in render error", error);
  }, [error]);

  return (
    <AppErrorState
      title="Sign-in error"
      description="The sign-in page hit an unexpected error. Try again, or reload the page."
      digest={error.digest}
      onRetry={retry}
    />
  );
}