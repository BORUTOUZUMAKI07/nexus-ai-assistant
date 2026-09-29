import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@/test/test-utils"
import { ArtifactCanvas } from "@/components/ArtifactCanvas"

// ArtifactCanvas fetches version history for persisted artifacts. Stub just that
// one call; everything else in the component is presentational.
const fetchArtifact = vi.fn()
vi.mock("@/lib/api", () => ({ fetchArtifact: (...args: unknown[]) => fetchArtifact(...args) }))

describe("ArtifactCanvas version history", () => {
  const persisted = {
    id: "art-1",
    title: "auth.py",
    language: "python",
    content: "CURRENT CONTENT",
    version: 3,
    isActiveVersion: true,
  }

  const transient = {
    id: "msg-1-artifact",
    title: "PYTHON Snippet",
    language: "python",
    content: "def hello():\n    return \"world\"\n",
  }

  const detailWithHistory = {
    ...persisted,
    versions: [
      { id: "v3", artifact_id: "art-1", version: 3, title: "auth.py", language: "python", mime_type: "text/x-python", content: "CURRENT CONTENT", created_at: "2026-01-03T10:00:00Z" },
      { id: "v2", artifact_id: "art-1", version: 2, title: "auth.py", language: "python", mime_type: "text/x-python", content: "OLDER CONTENT", created_at: "2026-01-02T10:00:00Z" },
      { id: "v1", artifact_id: "art-1", version: 1, title: "auth.py", language: "python", mime_type: "text/x-python", content: "OLDEST CONTENT", created_at: "2026-01-01T10:00:00Z" },
    ],
  }

  beforeEach(() => {
    fetchArtifact.mockReset()
    fetchArtifact.mockResolvedValue(detailWithHistory)
  })

  it("does not offer a history section for transient (unsaved) canvas items", () => {
    render(<ArtifactCanvas artifact={transient} onClose={vi.fn()} />)
    expect(screen.queryByText("Version history")).not.toBeInTheDocument()
    // No server row exists for a transient item, so it must not be requested.
    expect(fetchArtifact).not.toHaveBeenCalled()
  })

  it("lists the stored versions newest-first for a persisted artifact", async () => {
    render(<ArtifactCanvas artifact={persisted} onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByText("(3)")).toBeInTheDocument())
    expect(fetchArtifact).toHaveBeenCalledWith("art-1")

    const rows = screen.getAllByText(/^v\d$/)
    expect(rows.map((r) => r.textContent)).toEqual(["v3", "v2", "v1"])
  })

  it("previews an earlier version in the code view and can return to current", async () => {
    render(<ArtifactCanvas artifact={persisted} onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByText("(3)")).toBeInTheDocument())

    // Only past versions are previewable; the current one has no View button.
    const viewButtons = screen.getAllByRole("button", { name: /^View version/ })
    expect(viewButtons.map((b) => b.getAttribute("aria-label"))).toEqual([
      "View version 2",
      "View version 1",
    ])

    fireEvent.click(viewButtons[0])
    expect(screen.getByText("OLDER CONTENT")).toBeInTheDocument()
    expect(screen.queryByText("CURRENT CONTENT")).not.toBeInTheDocument()
    expect(
      screen.getByText(/Viewing version 2 — this is not the current version/)
    ).toBeInTheDocument()
    expect(screen.getByText("auth.py (v2)")).toBeInTheDocument()

    fireEvent.click(screen.getByText("Back to current"))
    expect(screen.getByText("CURRENT CONTENT")).toBeInTheDocument()
    expect(screen.queryByText(/this is not the current version/)).not.toBeInTheDocument()
  })

  it("keeps rendering the canvas when the history request fails", async () => {
    fetchArtifact.mockRejectedValue(new Error("offline"))
    render(<ArtifactCanvas artifact={persisted} onClose={vi.fn()} />)
    await waitFor(() =>
      expect(screen.getByText("No earlier versions recorded.")).toBeInTheDocument()
    )
    // The artifact itself is untouched by the failure.
    expect(screen.getByText("CURRENT CONTENT")).toBeInTheDocument()
  })

  it("does not fetch history for an artifact that has no stored row", async () => {
    fetchArtifact.mockResolvedValue({ ...persisted, versions: [] })
    render(<ArtifactCanvas artifact={transient} onClose={vi.fn()} />)
    expect(fetchArtifact).not.toHaveBeenCalled()
  })

  it("shows a loading state until the history request settles", async () => {
    // Held open so the pending state is observable, then released.
    let release!: (value: typeof detailWithHistory) => void
    fetchArtifact.mockReturnValue(
      new Promise<typeof detailWithHistory>((resolve) => {
        release = resolve
      }),
    )

    render(<ArtifactCanvas artifact={persisted} onClose={vi.fn()} />)
    // Nothing has resolved yet, so the count must not be shown as 0.
    expect(screen.getByText("loading…")).toBeInTheDocument()
    expect(screen.queryByText("(0)")).not.toBeInTheDocument()

    release(detailWithHistory)
    await waitFor(() => expect(screen.getByText("(3)")).toBeInTheDocument())
    expect(screen.queryByText("loading…")).not.toBeInTheDocument()
  })

  it("reloads and shows only the new artifact's history when switched", async () => {
    const second = {
      ...persisted,
      id: "art-2",
      title: "billing.py",
      content: "SECOND FILE",
      version: 1,
    }
    fetchArtifact.mockImplementation(async (id: string) =>
      id === "art-1" ? detailWithHistory : { ...second, versions: [] },
    )

    const { rerender } = render(
      <ArtifactCanvas artifact={persisted} onClose={vi.fn()} />
    )
    await waitFor(() => expect(screen.getByText("(3)")).toBeInTheDocument())

    rerender(<ArtifactCanvas artifact={second} onClose={vi.fn()} />)

    // The switch must not leave the first artifact's rows or content on screen.
    await waitFor(() =>
      expect(screen.getByText("No earlier versions recorded.")).toBeInTheDocument(),
    )
    expect(screen.getByText("SECOND FILE")).toBeInTheDocument()
    expect(screen.queryByText("CURRENT CONTENT")).not.toBeInTheDocument()
    expect(screen.getByText("billing.py")).toBeInTheDocument()
  })

  it("abandons an in-progress edit when the artifact is switched", async () => {
    const { rerender } = render(
      <ArtifactCanvas
        artifact={persisted}
        onClose={vi.fn()}
        onSaveVersion={vi.fn()}
      />,
    )
    await waitFor(() => expect(screen.getByText("(3)")).toBeInTheDocument())

    fireEvent.click(screen.getByRole("button", { name: /edit/i }))
    const editor = screen.getByRole("textbox", { name: /edit auth\.py/i })
    fireEvent.change(editor, { target: { value: "UNSAVED DRAFT" } })
    expect(screen.getByDisplayValue("UNSAVED DRAFT")).toBeInTheDocument()

    rerender(
      <ArtifactCanvas
        artifact={{ ...persisted, id: "art-2", title: "billing.py" }}
        onClose={vi.fn()}
        onSaveVersion={vi.fn()}
      />,
    )

    // The draft belonged to the file the user just left; carrying it over would
    // let them save one file's text over another. The editor is closed and the
    // read-only view of the new artifact is showing again.
    expect(screen.queryByRole("textbox", { name: /edit/i })).not.toBeInTheDocument()
    expect(screen.getByText("CURRENT CONTENT")).toBeInTheDocument()
    expect(screen.queryByText("UNSAVED DRAFT")).not.toBeInTheDocument()
  })

  it("still renders a canvas that has no artifact yet, without crashing", () => {
    // The canvas mounts before an artifact exists and is handed one a render
    // later. A hook that only ran on the second of those renders would change
    // the hook count between them and React would tear down the tree.
    const { rerender } = render(
      <ArtifactCanvas artifact={null as never} onClose={vi.fn()} />,
    )
    expect(() =>
      rerender(<ArtifactCanvas artifact={persisted} onClose={vi.fn()} />),
    ).not.toThrow()
    expect(screen.getByText("CURRENT CONTENT")).toBeInTheDocument()
  })
})
