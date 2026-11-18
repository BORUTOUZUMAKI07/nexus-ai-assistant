import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, fireEvent } from "@/test/test-utils"
import Home from "@/app/app/page"
import { sidebarLayoutKey } from "@/lib/sidebar-layout"

const { routerMock } = vi.hoisted(() => ({
  routerMock: { replace: vi.fn(), push: vi.fn() },
}))

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
}))

function clearCookie() {
  document.cookie.split(";").forEach((c) => {
    document.cookie = `${c.split("=")[0].trim()}=; path=/; max-age=0`
  })
}

async function login() {
  clearCookie()
  const view = render(<Home />)
  await screen.findByText("Welcome back")
  fireEvent.change(screen.getByPlaceholderText("name@example.com"), {
    target: { value: "test@nexus.ai" },
  })
  fireEvent.change(screen.getByPlaceholderText("••••••••"), {
    target: { value: "password123" },
  })
  fireEvent.click(screen.getByRole("button", { name: /Sign In/ }))
  await screen.findByText("Project kickoff")
  return view
}

describe("App shell hardening", () => {
  beforeEach(() => {
    routerMock.replace.mockClear()
    routerMock.push.mockClear()
    window.localStorage.clear()
  })

  it("sizes the shell to the dynamic viewport instead of the static screen", async () => {
    const { container } = await login()
    const shell = container.firstElementChild!
    expect(shell.className).toContain("h-dvh")
    expect(shell.className).toContain("min-h-dvh")
    expect(shell.className).not.toContain("h-screen")
    expect(shell.className).not.toContain("w-screen")
  })

  it("makes model comparison reachable from the small-screen header", async () => {
    await login()
    const compareButtons = screen.getAllByRole("button", {
      name: "Compare models side-by-side",
    })
    expect(compareButtons).toHaveLength(2)

    fireEvent.click(compareButtons[compareButtons.length - 1])
    expect(screen.getByRole("dialog", { name: "Model comparison" })).toBeInTheDocument()
  })

  it("toggles dark mode with the documented Cmd/Ctrl+Shift+D chord", async () => {
    await login()
    const before = document.documentElement.getAttribute("data-theme")
    fireEvent.keyDown(window, { key: "D", metaKey: true, shiftKey: true })
    const toggled = document.documentElement.getAttribute("data-theme")
    expect(toggled).not.toBe(before)
    fireEvent.keyDown(window, { key: "d", ctrlKey: true, shiftKey: true })
    expect(document.documentElement.getAttribute("data-theme")).toBe(before)
  })

  it("persists a user's sidebar dock and collapse choices", async () => {
    await login()

    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }))
    expect(JSON.parse(window.localStorage.getItem(sidebarLayoutKey("user-1"))!)).toEqual(
      expect.objectContaining({ collapsed: true })
    )

    fireEvent.click(screen.getByRole("button", { name: "Dock sidebar on the right" }))
    expect(JSON.parse(window.localStorage.getItem(sidebarLayoutKey("user-1"))!)).toEqual({
      dock: "right",
      collapsed: true,
    })

    const aside = screen.getByRole("complementary")
    expect(aside.parentElement!.className).toContain("md:order-2")
  })

  it("uses a semantic scrim for the mobile navigation backdrop", async () => {
    await login()
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }))
    const backdrop = screen.getByRole("button", { name: "Close navigation" })
    expect(backdrop.className).toContain("bg-[var(--overlay-scrim)]")
    expect(backdrop.className).not.toContain("bg-black/50")
  })
})
