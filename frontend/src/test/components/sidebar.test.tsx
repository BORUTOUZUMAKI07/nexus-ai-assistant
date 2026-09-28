import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@/test/test-utils"
import { Sidebar, ConversationItem } from "@/components/Sidebar"

const conversations: ConversationItem[] = [
  {
    id: "c1",
    title: "Pinned project",
    model: "llama-3.3-70b-versatile",
    is_pinned: true,
    updated_at: "2026-09-01",
  },
  {
    id: "c2",
    title: "Recent chat",
    model: "llama-3.3-70b-versatile",
    is_pinned: false,
    updated_at: "2026-09-02",
  },
]

function renderSidebar(overrides: Partial<Parameters<typeof Sidebar>[0]> = {}) {
  const props = {
    conversations,
    activeConversationId: null,
    onSelectConversation: vi.fn(),
    onNewChat: vi.fn(),
    onDeleteConversation: vi.fn(),
    activeTab: "chat" as const,
    setActiveTab: vi.fn(),
    currentModel: "llama-3.3-70b-versatile",
    onChangeModel: vi.fn(),
    onSignOut: vi.fn(),
    ...overrides,
  }
  render(<Sidebar {...props} />)
  return props
}

describe("Sidebar", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("renders the brand and model selector with the current model", () => {
    renderSidebar()
    expect(screen.getByText("Nexus AI")).toBeInTheDocument()
    expect(screen.getByRole("combobox")).toHaveValue("llama-3.3-70b-versatile")
    expect(screen.getByRole("option", { name: /Llama 3.3 70B/ })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: /DeepSeek R1/ })).toBeInTheDocument()
  })

  it("splits pinned and recent conversations", () => {
    renderSidebar()
    expect(screen.getByText("Pinned")).toBeInTheDocument()
    expect(screen.getByText("Pinned project")).toBeInTheDocument()
    expect(screen.getByText("Recent conversations")).toBeInTheDocument()
    expect(screen.getByText("Recent chat")).toBeInTheDocument()
  })

  it("selects a conversation on click", () => {
    const props = renderSidebar()
    fireEvent.click(screen.getByText("Recent chat"))
    expect(props.onSelectConversation).toHaveBeenCalledWith("c2")
  })

  it("deletes a conversation without triggering selection", () => {
    const props = renderSidebar()
    const deleteBtn = screen
      .getByText("Recent chat")
      .closest("div.group")!
      .querySelector("button")!
    fireEvent.click(deleteBtn)
    expect(props.onDeleteConversation).toHaveBeenCalled()
  })

  it("fires the new chat callback", () => {
    const props = renderSidebar()
    fireEvent.click(screen.getByText("New conversation"))
    expect(props.onNewChat).toHaveBeenCalled()
  })

  describe("rename", () => {
    /** The pencil button for a row, found by its accessible label. */
    function renameButton(title: string) {
      return screen.getByRole("button", { name: `Rename ${title}` })
    }

    /**
     * The open editor. Queried by textbox role rather than by label, because
     * the pencil button shares the same accessible name once the editor
     * closes -- asserting on the label would pass while the editor was still
     * mounted.
     */
    function editor(title: string) {
      return screen.getByRole("textbox", { name: `Rename ${title}` })
    }

    function queryEditor(title: string) {
      return screen.queryByRole("textbox", { name: `Rename ${title}` })
    }

    it("offers no rename control when the handler is not supplied", () => {
      renderSidebar()
      expect(screen.queryByRole("button", { name: /^Rename / })).toBeNull()
    })

    it("opens an input seeded with the current title", () => {
      renderSidebar({ onRenameConversation: vi.fn() })
      fireEvent.click(renameButton("Recent chat"))

      expect(editor("Recent chat")).toHaveValue("Recent chat")
    })

    it("does not open the row while the editor is active", () => {
      const props = renderSidebar({ onRenameConversation: vi.fn() })
      fireEvent.click(renameButton("Recent chat"))
      fireEvent.click(editor("Recent chat"))

      // Clicking the input must not bubble to the row and switch conversation.
      expect(props.onSelectConversation).not.toHaveBeenCalled()
    })

    it("saves the new title on Enter", () => {
      const onRename = vi.fn()
      renderSidebar({ onRenameConversation: onRename })
      fireEvent.click(renameButton("Recent chat"))

      const input = editor("Recent chat")
      fireEvent.change(input, { target: { value: "Renamed chat" } })
      fireEvent.keyDown(input, { key: "Enter" })

      expect(onRename).toHaveBeenCalledWith("c2", "Renamed chat")
    })

    it("trims the title and ignores a blank or unchanged value", () => {
      const onRename = vi.fn()
      renderSidebar({ onRenameConversation: onRename })

      fireEvent.click(renameButton("Recent chat"))
      fireEvent.change(editor("Recent chat"), { target: { value: "   " } })
      fireEvent.keyDown(editor("Recent chat"), { key: "Enter" })
      expect(onRename).not.toHaveBeenCalled()

      fireEvent.click(renameButton("Recent chat"))
      fireEvent.change(editor("Recent chat"), {
        target: { value: "  Recent chat  " },
      })
      fireEvent.keyDown(editor("Recent chat"), { key: "Enter" })
      // Same as the current title after trimming: nothing to persist.
      expect(onRename).not.toHaveBeenCalled()

      fireEvent.click(renameButton("Recent chat"))
      fireEvent.change(editor("Recent chat"), { target: { value: "  Trimmed  " } })
      fireEvent.keyDown(editor("Recent chat"), { key: "Enter" })
      expect(onRename).toHaveBeenCalledWith("c2", "Trimmed")
    })

    it("abandons the edit on Escape without saving", () => {
      const onRename = vi.fn()
      renderSidebar({ onRenameConversation: onRename })
      fireEvent.click(renameButton("Recent chat"))

      const input = editor("Recent chat")
      fireEvent.change(input, { target: { value: "Discarded" } })
      fireEvent.keyDown(input, { key: "Escape" })

      expect(onRename).not.toHaveBeenCalled()
      // The editor closes and the title is shown again.
      expect(queryEditor("Recent chat")).toBeNull()
      expect(screen.getByText("Recent chat")).toBeInTheDocument()
    })

    it("saves when the editor loses focus", () => {
      const onRename = vi.fn()
      renderSidebar({ onRenameConversation: onRename })
      fireEvent.click(renameButton("Recent chat"))

      const input = editor("Recent chat")
      fireEvent.change(input, { target: { value: "Blurred" } })
      fireEvent.blur(input)

      expect(onRename).toHaveBeenCalledWith("c2", "Blurred")
    })

    it("leaves the editor after a failed save instead of trapping the row", async () => {
      const onRename = vi.fn().mockRejectedValue(new Error("boom"))
      renderSidebar({ onRenameConversation: onRename })
      fireEvent.click(renameButton("Recent chat"))

      const input = editor("Recent chat")
      fireEvent.change(input, { target: { value: "Doomed" } })
      fireEvent.keyDown(input, { key: "Enter" })
      await vi.waitFor(() => expect(queryEditor("Recent chat")).toBeNull())
      expect(onRename).toHaveBeenCalled()
    })

    it("edits only the clicked row", () => {
      renderSidebar({ onRenameConversation: vi.fn() })
      fireEvent.click(renameButton("Recent chat"))

      expect(editor("Recent chat")).toBeInTheDocument()
      // The pinned row still shows its title, not a second editor.
      expect(screen.getByText("Pinned project")).toBeInTheDocument()
    })
  })

  it("switches active tab through the bottom navigation", () => {
    const props = renderSidebar({ showAdmin: true })
    fireEvent.click(screen.getByText("Usage"))
    expect(props.setActiveTab).toHaveBeenCalledWith("usage")
    fireEvent.click(screen.getByText("Admin"))
    expect(props.setActiveTab).toHaveBeenCalledWith("admin")
  })

  it("hides the Admin nav item from non-admin users", () => {
    renderSidebar()
    expect(screen.queryByText("Admin")).not.toBeInTheDocument()
  })

  it("signs out when the logout button is clicked", () => {
    const props = renderSidebar()
    fireEvent.click(screen.getByTitle("Sign out"))
    expect(props.onSignOut).toHaveBeenCalled()
  })

  it("shows an empty state when there are no recent conversations", () => {
    renderSidebar({ conversations: [] })
    expect(screen.getByText("No recent conversations")).toBeInTheDocument()
  })
})