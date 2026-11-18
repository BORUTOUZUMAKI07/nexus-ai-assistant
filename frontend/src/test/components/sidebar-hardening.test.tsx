import type { ComponentProps } from "react"
import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@/test/test-utils"
import { Sidebar, type ConversationItem } from "@/components/layout/Sidebar"

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

const archived: ConversationItem[] = [
  {
    id: "old",
    title: "Old chat",
    model: "llama-3.3-70b-versatile",
    is_pinned: false,
    updated_at: "2026-08-01",
  },
]

function renderSidebar(overrides: Partial<ComponentProps<typeof Sidebar>> = {}) {
  const props = {
    conversations,
    activeConversationId: "c2",
    onSelectConversation: vi.fn(),
    onNewChat: vi.fn(),
    onDeleteConversation: vi.fn(),
    activeTab: "chat" as const,
    setActiveTab: vi.fn(),
    currentModel: "complex_reasoning",
    onChangeModel: vi.fn(),
    onSignOut: vi.fn(),
    ...overrides,
  }
  render(<Sidebar {...props} />)
  return props
}

describe("Sidebar keyboard and dock hardening", () => {
  it("makes conversation rows keyboard operable and marks the active one", () => {
    const props = renderSidebar()
    const row = screen.getByRole("button", { name: "Open Recent chat" })

    expect(row).toHaveAttribute("aria-current", "page")
    expect(screen.getByRole("button", { name: "Open Pinned project" })).not.toHaveAttribute(
      "aria-current"
    )

    fireEvent.keyDown(row, { key: "Enter" })
    fireEvent.keyDown(row, { key: " " })
    expect(props.onSelectConversation).toHaveBeenNthCalledWith(1, "c2")
    expect(props.onSelectConversation).toHaveBeenNthCalledWith(2, "c2")
  })

  it("does not select a row when an inner control handles the key", () => {
    const props = renderSidebar({ onRenameConversation: vi.fn() })
    const rename = screen.getByRole("button", { name: "Rename Recent chat" })
    fireEvent.keyDown(rename, { key: "Enter" })
    expect(props.onSelectConversation).not.toHaveBeenCalled()
  })

  it("keeps hover-only row actions visible when they take focus", () => {
    renderSidebar({
      onRenameConversation: vi.fn(),
      onPinConversation: vi.fn(),
      onForkConversation: vi.fn(),
      onShareConversation: vi.fn(),
      onArchiveConversation: vi.fn(),
    })

    for (const name of [
      "Rename Recent chat",
      "Pin Recent chat",
      "Fork Recent chat",
      "Share Recent chat",
      "Archive Recent chat",
      "Delete Recent chat",
    ]) {
      expect(screen.getByRole("button", { name }).className).toContain(
        "focus-visible:opacity-100"
      )
    }
  })

  it("names icon-only actions for assistive technology", () => {
    renderSidebar({ onForkConversation: vi.fn(), onArchiveConversation: vi.fn() })
    expect(screen.getByRole("button", { name: "Fork Recent chat" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Archive Recent chat" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Delete Recent chat" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument()
  })

  it("makes archived rows restorable from the row itself", () => {
    const onRestoreConversation = vi.fn()
    renderSidebar({
      showArchived: true,
      onToggleArchived: vi.fn(),
      archivedConversations: archived,
      onRestoreConversation,
    })

    const row = screen.getByRole("button", { name: "Restore Old chat" })
    fireEvent.click(row)
    fireEvent.keyDown(row, { key: "Enter" })
    expect(onRestoreConversation).toHaveBeenCalledTimes(2)
    expect(onRestoreConversation).toHaveBeenCalledWith("old")
  })

  it("exposes labelled dock and collapse toggles", () => {
    const onChangeDock = vi.fn()
    const onToggleCollapsed = vi.fn()
    renderSidebar({
      dock: "right",
      onChangeDock,
      onToggleCollapsed,
    })

    const dock = screen.getByRole("button", { name: "Dock sidebar on the left" })
    const collapse = screen.getByRole("button", { name: "Collapse sidebar" })
    fireEvent.click(dock)
    fireEvent.click(collapse)
    expect(onChangeDock).toHaveBeenCalledWith("left")
    expect(onToggleCollapsed).toHaveBeenCalledTimes(1)
  })

  it("keeps primary actions reachable in the collapsed rail", () => {
    renderSidebar({ collapsed: true, onToggleCollapsed: vi.fn() })
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "New conversation" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Go to Settings" })).toBeInTheDocument()
  })
})
