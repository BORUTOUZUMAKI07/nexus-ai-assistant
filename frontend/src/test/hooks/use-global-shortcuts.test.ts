import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent } from "@testing-library/react"
import { renderHook } from "@/test/test-utils"
import { useGlobalShortcuts, type GlobalShortcutsOptions } from "@/hooks/use-global-shortcuts"

function setup(overrides: Partial<GlobalShortcutsOptions> = {}) {
  const options: GlobalShortcutsOptions = {
    onToggleHelp: vi.fn(),
    onCloseHelp: vi.fn(),
    onToggleTheme: vi.fn(),
    onStopGeneration: vi.fn(),
    isGenerating: false,
    helpOpen: false,
    overlaysOpen: false,
    ...overrides,
  }
  renderHook(() => useGlobalShortcuts(options))
  return options
}

describe("useGlobalShortcuts", () => {
  beforeEach(() => {
    document.body.innerHTML = ""
  })

  it("toggles the shortcuts guide with ? when not typing", () => {
    const options = setup()
    fireEvent.keyDown(window, { key: "?" })
    expect(options.onToggleHelp).toHaveBeenCalledTimes(1)
  })

  it("does not hijack ? typed into a text field", () => {
    const options = setup()
    const input = document.createElement("input")
    document.body.appendChild(input)
    fireEvent.keyDown(input, { key: "?" })
    expect(options.onToggleHelp).not.toHaveBeenCalled()
  })

  it("toggles dark mode with Cmd/Ctrl+Shift+D", () => {
    const meta = setup()
    fireEvent.keyDown(window, { key: "D", metaKey: true, shiftKey: true })
    expect(meta.onToggleTheme).toHaveBeenCalledTimes(1)

    const ctrl = setup()
    fireEvent.keyDown(window, { key: "d", ctrlKey: true, shiftKey: true })
    expect(ctrl.onToggleTheme).toHaveBeenCalledTimes(1)
  })

  it("stops an in-flight generation with Escape", () => {
    const options = setup({ isGenerating: true })
    fireEvent.keyDown(window, { key: "Escape" })
    expect(options.onStopGeneration).toHaveBeenCalledTimes(1)
  })

  it("does not stop generation while a dialog is open", () => {
    const options = setup({ isGenerating: true, overlaysOpen: true })
    fireEvent.keyDown(window, { key: "Escape" })
    expect(options.onStopGeneration).not.toHaveBeenCalled()
  })

  it("closes the shortcuts guide on Escape without stopping generation", () => {
    const options = setup({ isGenerating: true, helpOpen: true })
    fireEvent.keyDown(window, { key: "Escape" })
    expect(options.onCloseHelp).toHaveBeenCalledTimes(1)
    expect(options.onStopGeneration).not.toHaveBeenCalled()
    expect(options.onToggleHelp).not.toHaveBeenCalled()
  })
})
