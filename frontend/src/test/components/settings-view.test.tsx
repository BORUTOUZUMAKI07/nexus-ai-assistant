import { describe, it, expect } from "vitest"
import { render, screen, fireEvent, waitFor } from "@/test/test-utils"
import { SettingsView } from "@/components/SettingsView"

describe("SettingsView", () => {
  it("loads agent config, BYOK keys, and persistent memories from the API", async () => {
    render(<SettingsView />)

    expect(await screen.findByText("Agent configuration")).toBeInTheDocument()
    expect(screen.getByText("Bring-Your-Own-Key providers")).toBeInTheDocument()
    expect(screen.getByText("Persistent memories")).toBeInTheDocument()
    expect(screen.getByText(/Prefers Python and TypeScript/)).toBeInTheDocument()
    expect(screen.getByText("gsk_• • • • 1234")).toBeInTheDocument()
  })

  it("shows a saved confirmation after saving", async () => {
    render(<SettingsView />)
    await screen.findByText("Agent configuration")

    fireEvent.click(screen.getByRole("button", { name: /Save changes/ }))

    await waitFor(() => expect(screen.getByText("Saved")).toBeInTheDocument())
  })

  it("deletes a memory row", async () => {
    render(<SettingsView />)
    const memory = await screen.findByText(/Prefers Python and TypeScript/)
    const deleteBtn = memory
      .closest("div")!
      .parentElement!
      .querySelector("button")!
    fireEvent.click(deleteBtn)
    await waitFor(() =>
      expect(screen.queryByText(/Prefers Python and TypeScript/)).not.toBeInTheDocument()
    )
  })

  it("adds a memory through the API", async () => {
    render(<SettingsView />)
    await screen.findByText("Agent configuration")

    fireEvent.change(screen.getByPlaceholderText("E.g. Always respond with TypeScript examples"), {
      target: { value: "Likes concise summaries" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Add" }))

    await waitFor(() =>
      expect(screen.getByText("Likes concise summaries")).toBeInTheDocument()
    )
  })

  it("deletes a BYOK key row", async () => {
    render(<SettingsView />)
    await screen.findByText("Bring-Your-Own-Key providers")
    expect(screen.getByText(/gsk_/)).toBeInTheDocument()

    // The row's delete control is titled, so this does not depend on the icon.
    fireEvent.click(screen.getByTitle("Delete API key"))

    await waitFor(() =>
      expect(screen.queryByText(/gsk_/)).not.toBeInTheDocument()
    )
  })

  it("offers the real answer models (the same source as the sidebar picker)", async () => {
    render(<SettingsView />)
    await screen.findByText("Agent configuration")

    // A hardcoded list here would offer models the backend cannot route, which is
    // exactly the stale trio this replaced. The options come from lib/models.
    expect(screen.getByRole("option", { name: /Nexus Reasoning/ })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: /Nexus Fast/ })).toBeInTheDocument()
    expect(screen.queryByRole("option", { name: /DeepSeek/ })).not.toBeInTheDocument()
    expect(screen.queryByRole("option", { name: /Llama 3\.1 8B/ })).not.toBeInTheDocument()
  })
})