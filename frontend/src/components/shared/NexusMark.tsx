import type { CSSProperties } from "react";

/**
 * The Nexus brand glyph: a bold "N" whose diagonal is drawn as a chain of
 * nodes — "nexus" as a meeting point, and the agent graph in miniature.
 *
 * Rendered in `currentColor` so each call site controls the tint (usually
 * `--accent-ink`). This is deliberately a plain SVG rather than a lucide icon:
 * the previous assistant avatar was the generic lucide `Bot`, which the product
 * owner asked to replace ("the robot symbol in the chat is also not suitable").
 * Used for the assistant avatar, the landing navbar mark, and could back the
 * sidebar tile — one glyph, one identity.
 */
export function NexusMark({
  className,
  style,
}: {
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      className={className}
      style={style}
    >
      <path
        d="M7 18.5V5.5M17 18.5V5.5"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
      />
      <circle cx="7" cy="5.5" r="1.9" fill="currentColor" />
      <circle cx="12" cy="12" r="1.6" fill="currentColor" />
      <circle cx="17" cy="18.5" r="1.9" fill="currentColor" />
    </svg>
  );
}
