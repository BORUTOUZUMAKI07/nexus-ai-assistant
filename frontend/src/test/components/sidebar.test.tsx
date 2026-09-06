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
    expect(screen.getByText("NEXUS AI")).toBeInTheDocument()
    expect(screen.getByRole("combobox")).toHaveValue("llama-3.3-70b-versatile")
    expect(screen.getByRole("option", { name: /Llama 3.3 70B/ })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: /DeepSeek R1/ })).toBeInTheDocument()
  })

  it("splits pinned and recent conversations", () => {
    renderSidebar()
    expect(screen.getByText("Pinned")).toBeInTheDocument()
    expect(screen.getByText("Pinned project")).toBeInTheDocument()
    expect(screen.getByText("Recent Conversations")).toBeInTheDocument()
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
    fireEvent.click(screen.getByText("New Conversation"))
    expect(props.onNewChat).toHaveBeenCalled()
  })

  it("switches active tab through the bottom navigation", () => {
    const props = renderSidebar()
    fireEvent.click(screen.getByText("Usage & Free Tier Budget"))
    expect(props.setActiveTab).toHaveBeenCalledWith("usage")
    fireEvent.click(screen.getByText("Admin Center"))
    expect(props.setActiveTab).toHaveBeenCalledWith("admin")
  })

  it("signs out when the logout button is clicked", () => {
    const props = renderSidebar()
    fireEvent.click(screen.getByTitle("Sign out"))
    expect(props.onSignOut).toHaveBeenCalled()
  })

  it("shows an empty state when there are no recent conversations", () => {
    renderSidebar({ conversations: [] })
    expect(screen.getByText("No recent chats")).toBeInTheDocument()
  })
})