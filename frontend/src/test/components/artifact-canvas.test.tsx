import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@/test/test-utils"
import { ArtifactCanvas } from "@/components/ArtifactCanvas"

describe("ArtifactCanvas", () => {
  const persisted = {
    id: "art-1",
    title: "auth.py",
    language: "python",
    content: "def auth():\n    return True\n",
    version: 2,
    isActiveVersion: true,
  }

  const transient = {
    id: "msg-1-artifact",
    title: "PYTHON Snippet",
    language: "python",
    content: "def hello():\n    return \"world\"\n",
  }

  it("renders the artifact title and content in code view", () => {
    render(<ArtifactCanvas artifact={transient} onClose={vi.fn()} />)
    expect(screen.getByText("PYTHON Snippet")).toBeInTheDocument()
    expect(screen.getByText('def hello():')).toBeInTheDocument()
  })

  it("shows a persisted version badge with saved indicator", () => {
    render(<ArtifactCanvas artifact={persisted} onClose={vi.fn()} />)
    expect(screen.getByTitle("Persisted version 2")).toBeInTheDocument()
    expect(screen.getByText("v2 • saved")).toBeInTheDocument()
  })

  it("does not show a version badge for transient canvas items", () => {
    render(<ArtifactCanvas artifact={transient} onClose={vi.fn()} />)
    expect(screen.queryByText(/^v\d/)).not.toBeInTheDocument()
  })

  it("shows the delete button only for persisted artifacts and wires onDeleteArtifact", () => {
    const onDelete = vi.fn()
    const { unmount } = render(
      <ArtifactCanvas artifact={persisted} onClose={vi.fn()} onDeleteArtifact={onDelete} />
    )
    const deleteBtn = screen.getByTitle("Delete artifact and all versions")
    fireEvent.click(deleteBtn)
    expect(onDelete).toHaveBeenCalledWith(persisted)
    unmount()

    render(<ArtifactCanvas artifact={transient} onClose={vi.fn()} onDeleteArtifact={onDelete} />)
    expect(screen.queryByTitle("Delete artifact and all versions")).not.toBeInTheDocument()
  })
})