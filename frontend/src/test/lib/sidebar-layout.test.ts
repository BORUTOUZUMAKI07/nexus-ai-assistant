import { describe, it, expect, beforeEach } from "vitest"
import { act } from "@testing-library/react"
import { renderHook } from "@/test/test-utils"
import {
  useSidebarLayout,
  sidebarLayoutKey,
  DEFAULT_SIDEBAR_LAYOUT,
} from "@/lib/sidebar-layout"

describe("useSidebarLayout", () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it("defaults to a left dock and an expanded sidebar", () => {
    const { result } = renderHook(() => useSidebarLayout("user-1"))
    expect(result.current.layout).toEqual(DEFAULT_SIDEBAR_LAYOUT)
  })

  it("persists dock and collapsed state under a per-user key", () => {
    const { result } = renderHook(() => useSidebarLayout("user-1"))

    act(() => result.current.setDock("right"))
    act(() => result.current.toggleCollapsed())

    expect(result.current.layout).toEqual({ dock: "right", collapsed: true })
    expect(JSON.parse(window.localStorage.getItem(sidebarLayoutKey("user-1"))!)).toEqual({
      dock: "right",
      collapsed: true,
    })
  })

  it("isolates one user's layout from another's", () => {
    const { result, rerender } = renderHook(
      ({ userId }) => useSidebarLayout(userId),
      { initialProps: { userId: "user-1" } }
    )

    act(() => result.current.setDock("right"))
    rerender({ userId: "user-2" })
    expect(result.current.layout).toEqual(DEFAULT_SIDEBAR_LAYOUT)

    rerender({ userId: "user-1" })
    expect(result.current.layout).toEqual({ dock: "right", collapsed: false })
  })

  it("ignores malformed stored preferences instead of breaking the shell", () => {
    window.localStorage.setItem(sidebarLayoutKey("user-1"), "not json")
    const { result } = renderHook(() => useSidebarLayout("user-1"))
    expect(result.current.layout).toEqual(DEFAULT_SIDEBAR_LAYOUT)
  })
})
