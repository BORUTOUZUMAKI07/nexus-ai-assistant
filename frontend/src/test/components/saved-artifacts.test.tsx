import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@/test/test-utils"
import SavedArtifacts from "@/components/SavedArtifacts"
import type { ArtifactItem } from "@/components/ArtifactCanvas"

const saved: ArtifactItem = {
  id: "art-1",
  title: "auth.py",
  language: "python",
  content: "def auth():\n    return True\n",
  version: 2,
  isActiveVersion: true,
}

describe("SavedArtifacts", () => {
  it("renders nothing when the conversation has no saved artifacts", () => {
    const { container } = render(<SavedArtifacts artifacts={[]} onOpen={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it("lists saved artifacts with their language and version", () => {
    render(
      <SavedArtifacts
        artifacts={[
          saved,
          { id: "art-2", title: "schema.sql", language: "sql", content: "", version: 1 },
        ]}
        onOpen={vi.fn()}
      />
    )
    expect(screen.getByText("Saved artifacts")).toBeInTheDocument()
    expect(screen.getByText("2")).toBeInTheDocument()
    expect(screen.getByText("auth.py")).toBeInTheDocument()
    expect(screen.getByText("python · v2")).toBeInTheDocument()
    expect(screen.getByText("schema.sql")).toBeInTheDocument()
    expect(screen.getByText("sql · v1")).toBeInTheDocument()
  })

  it("opens the artifact that was clicked", () => {
    const onOpen = vi.fn()
    render(
      <SavedArtifacts
        artifacts={[
          saved,
          { id: "art-2", title: "schema.sql", language: "sql", content: "", version: 1 },
        ]}
        onOpen={onOpen}
      />
    )
    fireEvent.click(screen.getByText("schema.sql"))
    expect(onOpen).toHaveBeenCalledTimes(1)
    expect(onOpen).toHaveBeenCalledWith(
      expect.objectContaining({ id: "art-2", title: "schema.sql" })
    )
  })

  it("offers a single saved artifact, which the canvas tab bar cannot surface", () => {
    // ArtifactCanvas only renders its tab bar when more than one artifact is
    // open, so a lone saved artifact needs this strip to be reachable at all.
    render(<SavedArtifacts artifacts={[saved]} onOpen={vi.fn()} />)
    expect(screen.getByText("auth.py")).toBeInTheDocument()
    expect(screen.getByText("1")).toBeInTheDocument()
  })

  it("omits the version when the artifact is not persisted", () => {
    render(
      <SavedArtifacts
        artifacts={[{ id: "t-1", title: "Scratch", language: "python", content: "" }]}
        onOpen={vi.fn()}
      />
    )
    expect(screen.getByText("python")).toBeInTheDocument()
    expect(screen.queryByText(/· v\d/)).not.toBeInTheDocument()
  })
})
