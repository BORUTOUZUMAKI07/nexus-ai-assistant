/**
 * A token frame is a snapshot of the whole answer, not a delta. Writing every
 * snapshot to React state re-renders the transcript, sidebar, composer and the
 * Markdown parse of the growing answer tens of times a second. Coalescing keeps
 * only the newest snapshot per painted frame and loses nothing.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import { coalescePatches } from "@/lib/chatStream"

describe("coalescePatches", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("applies only the latest snapshot in an animation frame", () => {
    const scheduled: { cb?: (time: number) => void } = {}
    vi.spyOn(globalThis, "requestAnimationFrame").mockImplementation((cb) => {
      scheduled.cb = cb
      return 1
    })
    vi.spyOn(globalThis, "cancelAnimationFrame").mockImplementation(() => {})
    const apply = vi.fn()

    const { patch } = coalescePatches(apply)
    patch("hel", [])
    patch("hello ", [])
    patch("hello world", [])

    expect(apply).not.toHaveBeenCalled()
    scheduled.cb?.(0)

    expect(apply).toHaveBeenCalledTimes(1)
    expect(apply).toHaveBeenCalledWith("hello world", [])
  })

  it("flush applies the pending snapshot and cancels its frame", () => {
    const scheduled: { cb?: (time: number) => void } = {}
    const cancel = vi.spyOn(globalThis, "cancelAnimationFrame").mockImplementation(() => {})
    vi.spyOn(globalThis, "requestAnimationFrame").mockImplementation((cb) => {
      scheduled.cb = cb
      return 7
    })
    const apply = vi.fn()

    const { patch, flush } = coalescePatches(apply)
    patch("final answer", [])
    flush()
    scheduled.cb?.(0) // a cancelled frame must not apply anything twice

    expect(cancel).toHaveBeenCalledWith(7)
    expect(apply).toHaveBeenCalledTimes(1)
    expect(apply).toHaveBeenCalledWith("final answer", [])
  })

  it("keeps the explicit immediate mode for callers that opted in", () => {
    const raf = vi.spyOn(globalThis, "requestAnimationFrame")
    const apply = vi.fn()

    const { patch } = coalescePatches(apply, { immediate: true })
    patch("one", [])
    patch("two", [])

    expect(raf).not.toHaveBeenCalled()
    expect(apply).toHaveBeenCalledTimes(2)
  })
})
