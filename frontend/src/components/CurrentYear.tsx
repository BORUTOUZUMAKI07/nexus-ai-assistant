"use client";

import { useSyncExternalStore } from "react";

/**
 * The server has no idea what year it is.
 *
 * The landing page is a statically prerendered Server Component (`/` builds as
 * `○ (Static)`), so calling `new Date().getFullYear()` in its render baked the
 * *build* year into the HTML. That silently went stale at the next New Year and
 * stayed wrong until the next deploy.
 *
 * Making the whole landing page dynamic to fix one footer line would trade a
 * cosmetic defect for a real cost: no static optimisation and a slower TTFB on
 * the one page that has to look good for new visitors. So instead we push the
 * client boundary down to the smallest leaf that needs it, which is the right
 * rule for `use client` placement in the first place.
 *
 * `useSyncExternalStore` is the primitive for exactly this case -- a value that
 * legitimately differs between the server render and the client:
 *
 *  - `getServerSnapshot` returns a non-breaking space, so the prerendered HTML
 *    makes no claim about the year at all.
 *  - `getSnapshot` returns the real year once this hydrates on the client.
 *
 * React treats that server-snapshot-to-client-snapshot transition as expected
 * and re-renders without a hydration mismatch, so this needs no effect, no
 * state, and no `set-state-in-effect` suppression. The whitespace keeps the
 * footer from shifting when the real value lands.
 */
const SUBSCRIBE = () => () => {};

const getServerSnapshot = () => "\u00a0";

const getSnapshot = () => String(new Date().getFullYear());

export const CurrentYear: React.FC = () => (
  <>{useSyncExternalStore(SUBSCRIBE, getSnapshot, getServerSnapshot)}</>
);
