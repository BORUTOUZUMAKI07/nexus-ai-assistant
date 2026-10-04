/**
 * Rejoin: recovering an answer that was still running when the page went away.
 *
 * The backend decoupled the run from the request, so a turn keeps producing
 * frames whether or not the browser is watching. This is the client half — one
 * run id in localStorage per conversation, and a `GET` that replays it.
 *
 * What these tests are really guarding is the *bookkeeping*, because that is
 * where the feature dies quietly. Nothing throws when the run id is forgotten
 * early; the next reload simply shows an empty chat, which is indistinguishable
 * from the feature not existing. So each test below pins one decision about when
 * the id is written and when it is dropped.
 */
import { describe, it, expect, beforeEach } from "vitest"
import { renderHook, act, waitFor } from "@/test/test-utils"
import { useNexusChat } from "@/hooks/useNexusChat"
import { server } from "@/test/mocks/server"
import { http, HttpResponse } from "msw"

const CONV = "22222222-2222-4222-8222-222222222222"
const RUN = "11111111-1111-4111-8111-111111111111"
const STORAGE_KEY = `nexus-active-run:${CONV}`

const encoder = new TextEncoder()

/** The frames a replayed run produces: text, a citation, then the end. */
const REPLAY = '0:"the rest of "\n0:"the answer"\n'
  + `8:${JSON.stringify([{ type: "citation", data: { source: "docs", snippet: "s" } }])}\n`

function seedStoredRun(runId: string = RUN) {
  window.localStorage.setItem(STORAGE_KEY, runId)
}

function storedRun(): string | null {
  return window.localStorage.getItem(STORAGE_KEY)
}

describe("rejoin to a run already in flight", () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it("replays a stored run into the view without asking for a new answer", async () => {
    let requested = 0
    server.use(
      http.get("/api/chat", () => {
        requested += 1
        return new HttpResponse(REPLAY, {
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        })
      })
    )
    seedStoredRun()

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))

    await waitFor(() =>
      expect(result.current.messages.some((m) => m.content.includes("the answer"))).toBe(true)
    )
    // A resend would have been a second run producing different text; the point
    // of the durable log is that the answer the user saw is the answer they get.
    expect(requested).toBe(1)
    const recovered = result.current.messages.find((m) => m.role === "assistant")
    expect(recovered?.content).toBe("the rest of the answer")
    // The citation survives the replay: annotation handling is shared with the
    // live path, and a rejoin that dropped them would show a bare answer and
    // look like the feature only half works.
    expect(recovered?.annotations?.some((a) => a.type === "citation")).toBe(true)
  })

  it("asks for nothing when no run was ever recorded", async () => {
    // The common case by far. A hook that always issued the GET would spend a
    // request on every conversation switch and surface 404s as errors, which is
    // how a recovery feature becomes the thing users complain about.
    let requested = 0
    server.use(
      http.get("/api/chat", () => {
        requested += 1
        return new HttpResponse("")
      })
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await act(async () => {
      await Promise.resolve()
    })

    expect(requested).toBe(0)
    expect(result.current.messages).toEqual([])
    expect(result.current.error).toBeNull()
  })

  it("forgets the run id once the replay reaches the end", async () => {
    // Otherwise every subsequent mount re-attaches to a finished run and the
    // answer reappears on every visit, which reads as the app being haunted.
    server.use(
      http.get("/api/chat", () => new HttpResponse(REPLAY))
    )
    seedStoredRun()

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await waitFor(() => expect(storedRun()).toBeNull())
    expect(result.current.error).toBeNull()
  })

  it("keeps the run id when the replay could not complete", async () => {
    // The negative case, and the one that makes the feature worth having: a
    // flaky connection must not cost the user the only handle to an answer that
    // is still being written. Forgetting here would leave no trace that the run
    // existed, and the next reload would have nothing to ask for.
    server.use(
      http.get("/api/chat", () => HttpResponse.error())
    )
    seedStoredRun()

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(storedRun()).toBe(RUN)
    // An empty assistant bubble is worse than none: it reads as a turn that
    // produced nothing rather than a recovery that did not happen.
    expect(result.current.messages).toEqual([])
  })

  it("forgets a run the backend no longer has, and shows no bubble", async () => {
    // 404 is the one rejoin failure that is not an error. Retrying it forever
    // would be wrong, and telling the user about it would be alarming — it is
    // their own conversation.
    server.use(
      http.get("/api/chat", () =>
        new HttpResponse(JSON.stringify({ detail: "run_not_found" }), { status: 404 })
      )
    )
    seedStoredRun()

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await waitFor(() => expect(storedRun()).toBeNull())
    expect(result.current.messages).toEqual([])
    expect(result.current.error).toBeNull()
  })

  it("ignores a stored value that is not a run id", async () => {
    // localStorage is user-visible and survives deploys. A key from an older
    // schema, or a hand-edit, must not become a request that 400s.
    let requested = 0
    server.use(
      http.get("/api/chat", () => {
        requested += 1
        return new HttpResponse("")
      })
    )
    seedStoredRun("garbage")

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await act(async () => {
      await Promise.resolve()
    })

    expect(requested).toBe(0)
    expect(result.current.error).toBeNull()
  })

  it("does not re-attach while another turn still owns the conversation", async () => {
    // Switching conversations mid-answer is ordinary: the sidebar is one click
    // away. Without the in-flight guard the new conversation's stored run would
    // be joined *while the old turn is still streaming*, putting one run's frames
    // into another run's bubble and putting two runs against one backend thread
    // slot — which the backend answers by 409ing one of them.
    //
    // The fixture keeps the first turn's stream open, so `loadingRef` is still
    // held for the whole window; a test that let it close first would not reach
    // the branch.
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          new ReadableStream({
            start(controller) {
              controller.enqueue(encoder.encode('0:"still going"'))
              void gate.then(() => controller.close())
            },
          })
        )
      )
    )
    let rejoins = 0
    server.use(
      http.get("/api/chat", () => {
        rejoins += 1
        return new HttpResponse(REPLAY)
      })
    )
    const OTHER = "33333333-3333-4333-8333-333333333333"
    window.localStorage.setItem(`nexus-active-run:${OTHER}`, RUN)

    const { result, rerender } = renderHook(
      ({ conversationId }: { conversationId: string }) =>
        useNexusChat({ conversationId }),
      { initialProps: { conversationId: CONV } }
    )

    let first!: Promise<void>
    act(() => {
      first = result.current.sendMessage("hello")
    })
    await waitFor(() => expect(result.current.isLoading).toBe(true))

    act(() => {
      rerender({ conversationId: OTHER })
    })
    await act(async () => {
      await Promise.resolve()
    })
    expect(rejoins).toBe(0)

    await act(async () => {
      release()
      await first
    })
  })

  it("records the run id a turn started with, so it can be recovered", async () => {
    // The write side. Without it the rejoin has nothing to re-attach to and the
    // GET is never made, so this is the single test that would catch the whole
    // feature being unreachable.
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          new ReadableStream({
            start(controller) {
              controller.enqueue(encoder.encode('0:"answer"'))
              controller.close()
            },
          }),
          {
            headers: {
              "Content-Type": "text/plain; charset=utf-8",
              "X-Nexus-Run-Id": RUN,
            },
          }
        )
      )
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await act(async () => {
      await result.current.sendMessage("hello")
    })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    expect(result.current.messages.find((m) => m.role === "assistant")?.content).toBe(
      "answer"
    )
    // Written during the turn, cleared by its clean end — so a *finished* turn
    // leaves nothing behind. The value is observed mid-flight below.
    expect(storedRun()).toBeNull()
  })

  it("still holds the run id while the turn is in flight", async () => {
    // The window the whole feature depends on: between "started" and "finished".
    // Asserting only the post-turn state above would pass even if the write
    // never happened and the clear always ran, because both leave null.
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          new ReadableStream({
            start(controller) {
              controller.enqueue(encoder.encode('0:"partial"'))
              void gate.then(() => controller.close())
            },
          }),
          {
            headers: {
              "Content-Type": "text/plain; charset=utf-8",
              "X-Nexus-Run-Id": RUN,
            },
          }
        )
      )
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    let first!: Promise<void>
    act(() => {
      first = result.current.sendMessage("hello")
    })

    await waitFor(() => expect(storedRun()).toBe(RUN))

    await act(async () => {
      release()
      await first
    })
    await waitFor(() => expect(storedRun()).toBeNull())
  })
})