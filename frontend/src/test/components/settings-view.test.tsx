import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@/test/test-utils"
import { SettingsView } from "@/components/SettingsView"

describe("SettingsView", () => {
  it("renders BYOK vault, system prompt, and persistent memories", () => {
    render(<SettingsView />)
    expect(screen.getByText("Bring Your Own Key (BYOK) Encryption Vault")).toBeInTheDocument()
    expect(screen.getByText("Global System Instructions")).toBeInTheDocument()
    expect(screen.getByText("Persistent User Memories")).toBeInTheDocument()
    expect(screen.getByDisplayValue(/You are Nexus AI/)).toBeInTheDocument()
  })

  it("shows a saved confirmation after saving", () => {
    vi.useFakeTimers()
    render(<SettingsView />)
    fireEvent.click(screen.getByRole("button", { name: /Save Settings/ }))
    expect(screen.getByText("Saved!")).toBeInTheDocument()
    vi.useRealTimers()
  })

  it("deletes a memory row", () => {
    render(<SettingsView />)
    const memory = screen.getByText(/Prefers Python and TypeScript/)
    const deleteBtn = memory.closest("div")!.parentElement!.querySelector("button")!
    fireEvent.click(deleteBtn)
    expect(screen.queryByText(/Prefers Python and TypeScript/)).not.toBeInTheDocument()
  })
})