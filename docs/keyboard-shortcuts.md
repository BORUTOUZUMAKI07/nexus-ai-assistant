# Nexus AI Assistant — Keyboard Shortcuts

> Every entry below was verified against source at `1f43c89`
> (`frontend/src/components/CommandPalette.tsx`,
> `frontend/src/components/ArtifactCanvas.tsx`,
> `frontend/src/components/ChatInput.tsx`). These are the **only** keyboard
> shortcuts the app binds today.

## Chat input

| Shortcut | Effect | Source |
|---|---|---|
| `Enter` | Send message | `ChatInput.tsx:137` (`e.key === "Enter" && !e.shiftKey`) |
| `Shift+Enter` | Insert a newline (send is suppressed) | same handler |
| `↑` / `↓` | Navigate the slash-command menu (when open) | `ChatInput.tsx:117-125` |
| `Tab` / `Enter` | Apply the highlighted slash command | `ChatInput.tsx:127-131` |
| `Esc` | Dismiss the slash-command menu | `ChatInput.tsx:132-135` |

Slash menu: type `/` in the input to open it (e.g. `/code` enables code
execution mode).

## Global

| Shortcut | Effect | Source |
|---|---|---|
| `⌘K` / `Ctrl+K` | Toggle the command palette | `CommandPalette.tsx:68` (`(metaKey ∥ ctrlKey) && key.toLowerCase() === "k"`) |

## Artifact canvas

| Shortcut | Effect | Source |
|---|---|---|
| `⌘F` / `Ctrl+F` | Open find-in-artifact search and focus it | `ArtifactCanvas.tsx:92` |
| `Esc` | Close search, clear the query, and abandon an in-progress edit | `ArtifactCanvas.tsx:97-104` |
| `⌘S` / `Ctrl+S` | Save the in-progress edit (replaces the browser save-page dialog) | `ArtifactCanvas.tsx:108-113` (only while editing) |

---

**Note:** older versions of this document listed `Ctrl+Shift+O`,
`Ctrl+Shift+B`, `Ctrl+Shift+C`, `Ctrl+Shift+W`, and `Ctrl+/` shortcuts. None of
those exist in the current codebase — they were removed/never implemented, so
they are intentionally not documented here.