"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Clipboard writes that cannot throw into the caller's event handler.
 *
 * The three call sites that used to do `navigator.clipboard.writeText(x)`
 * bare had two problems:
 *
 *  1. `navigator.clipboard` is `undefined` outside a secure context. The
 *     Dockerfile binds `HOSTNAME=0.0.0.0`, which invites reaching the app over
 *     `http://<lan-ip>:3000` — and in that case the property is simply not
 *     there. Reading `.writeText` off `undefined` is a synchronous `TypeError`
 *     inside the click handler: uncaught, no boundary above it, and the Copy
 *     button silently does nothing.
 *  2. Even where it exists, `writeText` returns a promise that rejects when the
 *     document is not focused or permission is denied. Nobody was awaiting it,
 *     so it surfaced as an unhandled rejection in the console while the UI still
 *     claimed success.
 *
 * Returns `true` only when the text actually reached the clipboard, so callers
 * can avoid showing a "Copied!" badge for a copy that did not happen.
 */
export async function writeToClipboard(text: string): Promise<boolean> {
  try {
    const clipboard = typeof navigator === "undefined" ? undefined : navigator.clipboard;
    if (!clipboard) return false;
    await clipboard.writeText(text);
    return true;
  } catch {
    // Permission denied, document not focused, or a mid-write abort. Copy is a
    // convenience; failing it must never surface as an unhandled rejection.
    return false;
  }
}

/**
 * A "Copied!" acknowledgement that clears itself and cannot leak.
 *
 * Each call site previously armed its own bare `setTimeout` and never cleared
 * it, so the timer survived the component being unmounted — which happens
 * routinely for a code block, because the markdown split re-keys blocks as a
 * streamed answer grows. The unmount then ran `setCopied` on a dead component
 * and the timer was orphaned.
 *
 * Clearing on cleanup, and cancelling an in-flight acknowledgement when a new
 * copy starts, makes the badge a strict property of the mounted component.
 */
export function useCopyFeedback(
  resetAfterMs = 2000,
): [string | null, (id: string, text: string) => void] {
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    [],
  );

  const copy = useCallback(
    (id: string, text: string) => {
      void writeToClipboard(text).then((ok) => {
        // A failed write must not claim success, so leave the badge alone.
        if (!ok) return;
        setCopiedId(id);
        if (timerRef.current) clearTimeout(timerRef.current);
        timerRef.current = setTimeout(() => setCopiedId(null), resetAfterMs);
      });
    },
    [resetAfterMs],
  );

  return [copiedId, copy];
}

/**
 * Boolean form of {@link useCopyFeedback}, for a component that owns a single
 * copy target (a code block's own "Copied!" badge) rather than tracking which
 * of many targets was last copied.
 */
export function useCopyToggle(
  resetAfterMs = 2000,
): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    [],
  );

  const copy = useCallback(
    (text: string) => {
      void writeToClipboard(text).then((ok) => {
        if (!ok) return;
        setCopied(true);
        if (timerRef.current) clearTimeout(timerRef.current);
        timerRef.current = setTimeout(() => setCopied(false), resetAfterMs);
      });
    },
    [resetAfterMs],
  );

  return [copied, copy];
}
