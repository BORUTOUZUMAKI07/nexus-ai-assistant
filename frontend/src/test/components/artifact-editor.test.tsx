/**
 * The artifact editor: the control that makes addArtifactVersion reachable.
 *
 * The api function existed and the backend endpoint existed, but nothing in the
 * UI could trigger either -- the canvas was read-only. These tests cover the
 * editor itself, in particular the cases where a wrong implementation would
 * silently destroy work: switching artifacts mid-edit, saving over a past
 * version, and a failed save that discards the buffer.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@/test/test-utils"
import { ArtifactCanvas, ArtifactItem } from "@/components/ArtifactCanvas"

const SAVED: ArtifactItem = {
  id: "art-1",
  title: "report.md",
  language: "markdown",
  content: "# v1\noriginal",
  // Must be higher than the version the MSW history handler returns (1): the
  // canvas only offers a "View" button for a version that is not the current
  // one, so a matching number would make the history look unclickable.
  version: 2,
  isActiveVersion: true,
}

const TRANSIENT: ArtifactItem = {
  id: "transient-1",
  title: "scratch.ts",
  language: "typescript",
  content: "const a = 1;",
}

function renderCanvas(overrides: Partial<Parameters<typeof ArtifactCanvas>[0]> = {}) {
  const props = {
    artifact: SAVED,
    onClose: vi.fn(),
    onSaveVersion: vi.fn().mockResolvedValue({ ...SAVED, version: 2 }),
    ...overrides,
  }
  render(<ArtifactCanvas {...props} />)
  return props
}

// Queried by accessible name, not by title: the tooltip changes when a past
// version is being previewed, and the control must stay findable (disabled)
// rather than appear to disappear.
const editButton = () =>
  screen.getByRole("button", { name: "Edit and save a new version" })

const queryEditButton = () =>
  screen.queryByRole("button", { name: "Edit and save a new version" })
const editor = () => screen.getByRole("textbox", { name: /Edit / })
const saveButton = () => screen.getByText("Save new version")

describe("ArtifactCanvas editing", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  describe("availability", () => {
    it("offers Edit on a saved artifact when a saver is wired", () => {
      renderCanvas()
      expect(editButton()).toBeInTheDocument()
    })

    it("offers no Edit when no saver is provided", () => {
      // Without a saver there is nowhere to write a version, so the control
      // must not appear and lead to a dead end.
      renderCanvas({ onSaveVersion: undefined })
      expect(queryEditButton()).toBeNull()
    })

    it("offers no Edit on a transient artifact even with a saver", () => {
      // A transient item has no server-side id; saving would 404.
      renderCanvas({ artifact: TRANSIENT })
      expect(queryEditButton()).toBeNull()
    })

    it("disables Edit while previewing an older version", async () => {
      renderCanvas()
      // Expand history and switch to a past version. The component fetches the
      // history for art-1, so this waits on MSW rather than assuming it is
      // already in the DOM.
      fireEvent.click(screen.getByText("Version history"))
      const viewBtn = await screen.findByTitle("View version 1")
      fireEvent.click(viewBtn)

      await waitFor(() =>
        expect(screen.getByText("Back to current")).toBeInTheDocument()
      )
      expect(editButton()).toBeDisabled()
    })
  })

  it("opens an editor seeded with the current content", () => {
    renderCanvas()
    fireEvent.click(editButton())
    expect(editor()).toHaveValue("# v1\noriginal")
  })

  it("replaces the read-only view with a textarea while editing", () => {
    renderCanvas()
    fireEvent.click(editButton())
    expect(editor()).toBeInTheDocument()
    // The code view's line-number gutter is replaced, not stacked on top.
    expect(screen.queryByText("Preview")).toBeInTheDocument()
  })

  it("saves the edited text and reports the new version", async () => {
    const onSaveVersion = vi.fn().mockResolvedValue({ ...SAVED, version: 3 })
    renderCanvas({ onSaveVersion })

    fireEvent.click(editButton())
    fireEvent.change(editor(), { target: { value: "# v1\nrevised" } })
    fireEvent.click(saveButton())

    await waitFor(() =>
      expect(onSaveVersion).toHaveBeenCalledWith(
        expect.objectContaining({ id: "art-1" }),
        "# v1\nrevised",
      )
    )
    await waitFor(() => expect(screen.getByText("Saved as version 3")).toBeInTheDocument())
    // Editor closes on success.
    expect(screen.queryByRole("textbox", { name: /Edit / })).toBeNull()
  })

  it("keeps the buffer and stays open when the save fails", async () => {
    const onSaveVersion = vi.fn().mockRejectedValue(new Error("Version save failed: 500"))
    renderCanvas({ onSaveVersion })

    fireEvent.click(editButton())
    fireEvent.change(editor(), { target: { value: "work in progress" } })
    fireEvent.click(saveButton())

    await waitFor(() =>
      expect(screen.getByText(/Version save failed: 500/)).toBeInTheDocument()
    )
    // The unsaved work is still on screen -- this is the whole point.
    expect(editor()).toHaveValue("work in progress")
    expect(onSaveVersion).toHaveBeenCalledTimes(1)
  })

  it("discards the draft on Cancel without calling the saver", async () => {
    const onSaveVersion = vi.fn()
    renderCanvas({ onSaveVersion })

    fireEvent.click(editButton())
    fireEvent.change(editor(), { target: { value: "throwaway" } })
    fireEvent.click(screen.getByText("Cancel"))

    expect(onSaveVersion).not.toHaveBeenCalled()
    expect(screen.queryByRole("textbox", { name: /Edit / })).toBeNull()
  })

  it("refuses to burn a version number on unchanged text", () => {
    const onSaveVersion = vi.fn()
    renderCanvas({ onSaveVersion })

    fireEvent.click(editButton())
    // No edits at all.
    fireEvent.click(saveButton())

    expect(onSaveVersion).not.toHaveBeenCalled()
    expect(screen.getByText("No changes to save.")).toBeInTheDocument()
  })

  it("abandons the edit on Escape", () => {
    const onSaveVersion = vi.fn()
    renderCanvas({ onSaveVersion })

    fireEvent.click(editButton())
    fireEvent.change(editor(), { target: { value: "discarded" } })
    // Dispatched on the textarea, which is where the key event actually
    // originates; it bubbles to the window listener in a real browser.
    fireEvent.keyDown(editor(), { key: "Escape" })

    expect(onSaveVersion).not.toHaveBeenCalled()
    expect(screen.queryByRole("textbox", { name: /Edit / })).toBeNull()
  })

  it("saves on Ctrl+S instead of the browser save dialog", async () => {
    const onSaveVersion = vi.fn().mockResolvedValue({ ...SAVED, version: 2 })
    renderCanvas({ onSaveVersion })

    fireEvent.click(editButton())
    fireEvent.change(editor(), { target: { value: "shortcut" } })
    fireEvent.keyDown(editor(), { key: "s", ctrlKey: true })

    // Synchronous first: if the handler never ran this reports that plainly,
    // instead of timing out inside waitFor and hiding the real cause.
    expect(onSaveVersion).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.getByText("Saved as version 2")).toBeInTheDocument())
  })

  it("abandons an unsaved draft when a different artifact is opened", () => {
    // Regression guard: state that was per-artifact used to survive the switch,
    // so opening artifact B would show artifact A's draft under B's title.
    const onSaveVersion = vi.fn()
    const { rerender } = render(
      <ArtifactCanvas
        artifact={SAVED}
        artifacts={[SAVED, { ...SAVED, id: "art-2", title: "other.md" }]}
        onClose={vi.fn()}
        onSaveVersion={onSaveVersion}
      />
    )

    fireEvent.click(editButton())
    fireEvent.change(editor(), { target: { value: "A's unsaved draft" } })

    rerender(
      <ArtifactCanvas
        artifact={{ ...SAVED, id: "art-2", title: "other.md" }}
        artifacts={[SAVED, { ...SAVED, id: "art-2", title: "other.md" }]}
        onClose={vi.fn()}
        onSaveVersion={onSaveVersion}
      />
    )

    // Back to read-only on the new artifact, with no trace of the old draft.
    // The header h3 is checked rather than the title text, because the tab bar
    // also renders the name once multiple artifacts are present.
    expect(screen.queryByRole("textbox", { name: /Edit / })).toBeNull()
    expect(screen.getByRole("heading", { name: "other.md" })).toBeInTheDocument()
    expect(screen.queryByText("A's unsaved draft")).toBeNull()
  })

  it("keeps the line-number gutter in step with the draft", () => {
    // A gutter derived from the saved content while the textarea shows the
    // draft would be silently wrong for every edited file.
    renderCanvas()
    fireEvent.click(editButton())
    fireEvent.change(editor(), { target: { value: "one\ntwo\nthree" } })

    expect(screen.getByText("1")).toBeInTheDocument()
    expect(screen.getByText("3")).toBeInTheDocument()
  })

  it("hides version history while editing so history cannot be misread as current", () => {
    renderCanvas()
    expect(screen.getByText("Version history")).toBeInTheDocument()
    fireEvent.click(editButton())
    expect(screen.queryByText("Version history")).toBeNull()
  })
})
