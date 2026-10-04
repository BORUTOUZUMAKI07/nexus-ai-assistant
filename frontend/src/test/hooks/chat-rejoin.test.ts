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
// A second conversation, for the switch test. A different id rather than a
// parameterised hook, because the point is that a *load* was already in flight
// for the other one.
const OTHER = "44444444-4444-4444-8444-444444444444"
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

/**
 * The conversation detail the hook fetches when a conversation is opened.
 *
 * Overridden in `beforeEach` for every test here, and *empty* by default. That is
 * deliberate: the hook now hydrates the transcript before it joins, so a shared
 * handler returning the mock conversation's two messages would put them into
 * every one of these views and the assertions below ("no bubble", "nothing
 * rendered") would be measuring the wrong thing. A test that wants a populated
 * transcript says so by serving one.
 */
function serveHistory(messages: unknown[] = []) {
  server.use(
    http.get("/api/conversations/:id", () =>
      HttpResponse.json({
        id: CONV,
        title: "t",
        system_prompt: null,
        messages,
      })
    )
  )
}

/** A stored conversation row in the shape `messagesFromHistory` reads. */
function row(over: Record<string, unknown> = {}) {
  return {
    id: "msg-1",
    role: "user",
    content: "an earlier question",
    thought_process: null,
    model: null,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    citations: [],
    tool_calls: [],
    user_feedback: null,
    created_at: "2026-10-04T00:00:00Z",
    ...over,
  }
}

describe("rejoin to a run already in flight", () => {
  beforeEach(() => {
    window.localStorage.clear()
    serveHistory()
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

describe("history and rejoin are one writer", () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it("puts the recovered answer below the turn that produced it", async () => {
    // The whole point of loading history. A rejoin on its own recovers the answer
    // and nothing else, so the transcript read as a reply appearing out of
    // nowhere -- which is why the earlier version of this file asserted against
    // an empty list. The order is what carries the meaning, so both halves are
    // asserted: the rows are present *and* they precede the replay.
    serveHistory([
      row({ id: "u1", role: "user", content: "what is RAG?" }),
      row({ id: "a1", role: "assistant", content: "the older answer" }),
    ])
    server.use(http.get("/api/chat", () => new HttpResponse(REPLAY)))
    seedStoredRun()

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await waitFor(() =>
      expect(result.current.messages.some((m) => m.content.includes("the answer"))).toBe(true)
    )

    expect(result.current.messages.map((m) => m.id)).toEqual(["u1", "a1", expect.stringContaining("msg-rejoined")])
    expect(result.current.messages[0].content).toBe("what is RAG?")
  })

  it("does not replay a finished run whose answer history already has", async () => {
    // The duplicate-answer case, and the reason the backend had to be asked. A
    // run that completed while the page was closed *has* its answer in the
    // conversation, and the replay starts at frame 0 -- so without this the user
    // reads the same answer twice and the transcript disagrees with the
    // database about how many turns happened.
    serveHistory([
      row({ id: "u1", role: "user", content: "what is RAG?" }),
      row({ id: "a1", role: "assistant", content: "the rest of the answer" }),
    ])
    let replays = 0
    server.use(
      http.get("/api/chat", () => {
        replays += 1
        return new HttpResponse(REPLAY, {
          headers: { "X-Nexus-Message-Id": "a1" },
        })
      })
    )
    seedStoredRun()

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await waitFor(() => expect(storedRun()).toBeNull())

    expect(result.current.messages.map((m) => m.id)).toEqual(["u1", "a1"])
    // The request was still made -- it has to be, to learn the id. What matters
    // is that no *bubble* came from it, which the id list already proves; the
    // counter pins that the GET is one request and not a retry loop.
    expect(replays).toBe(1)
  })

  it("still replays when the run finished but its answer is not on screen", async () => {
    // The guard above must not become "never replay". A run that wrote its row
    // *after* this history was fetched has an id the transcript does not contain,
    // and dropping the replay on that basis loses the answer the user was waiting
    // for -- which is the feature. Same header, opposite verdict, decided only by
    // the transcript.
    serveHistory([row({ id: "u1", role: "user", content: "what is RAG?" })])
    server.use(
      http.get("/api/chat", () =>
        new HttpResponse(REPLAY, { headers: { "X-Nexus-Message-Id": "a-new" } })
      )
    )
    seedStoredRun()

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await waitFor(() =>
      expect(result.current.messages.some((m) => m.content.includes("the answer"))).toBe(true)
    )
    expect(result.current.messages).toHaveLength(2)
  })

  it("does not let a late history load delete a turn the user just sent", async () => {
    // The reverse ordering, and the reason the loader is fenced. Conversation
    // ownership alone cannot catch this one: it is the *same* conversation, and
    // what moved is the transcript -- the user typed and sent while the fetch was
    // still open. Applying the fetched rows then wipes their message and any
    // streamed answer, and the app looks like it dropped the turn.
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.get("/api/conversations/:id", async () => {
        await gate
        return HttpResponse.json({ id: CONV, system_prompt: null, messages: [] })
      })
    )
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          new ReadableStream({
            start(controller) {
              controller.enqueue(encoder.encode('0:"fresh answer"'))
              controller.close()
            },
          })
        )
      )
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    // The send happens while the history request is still parked.
    await act(async () => {
      await result.current.sendMessage("hello")
    })
    expect(result.current.messages.some((m) => m.role === "assistant")).toBe(true)

    await act(async () => {
      release()
      await gate
    })

    // Still there after the history lands. An empty fetched transcript must not
    // be able to blank a conversation that has since been written to.
    await waitFor(() =>
      expect(result.current.messages.some((m) => m.content === "fresh answer")).toBe(true)
    )
  })

  it("discards a history load the conversation switch made stale", async () => {
    // The reason the claim is taken *before* the first await. Two conversations
    // selected in quick succession issue two loads; the slower one belongs to a
    // conversation the user has already left, and applying it renders one
    // conversation's messages under another's name. Nothing throws -- the app
    // simply shows the wrong conversation, which is worse than showing none.
    //
    // Both halves are load-bearing and are asserted separately, because "B is on
    // screen" also passes when A was never fetched at all: `expect(messages).not.
    // toContain(A)` is what says A's load actually resolved and was rejected.
    let releaseA!: () => void
    const gateA = new Promise<void>((resolve) => {
      releaseA = resolve
    })
    let sawA = false

    server.use(
      http.get("/api/conversations/:id", async ({ params }) => {
        if (params.id === CONV) {
          sawA = true
          await gateA
          return HttpResponse.json({
            id: CONV,
            system_prompt: null,
            messages: [row({ id: "msg-a", content: "conversation A" })],
          })
        }
        return HttpResponse.json({
          id: OTHER,
          system_prompt: null,
          messages: [row({ id: "msg-b", content: "conversation B" })],
        })
      })
    )

    const { result, rerender } = renderHook(
      ({ id }: { id: string }) => useNexusChat({ conversationId: id }),
      { initialProps: { id: CONV } }
    )
    // Let the effect for A get as far as its `await` before switching, otherwise
    // this passes for the wrong reason -- the load is only stale if it started.
    await waitFor(() => expect(sawA).toBe(true))

    rerender({ id: OTHER })
    await waitFor(() =>
      expect(result.current.messages.some((m) => m.content === "conversation B")).toBe(true)
    )

    await act(async () => {
      releaseA()
      await gateA
    })

    // A's rows arrive last and must be dropped. If they were applied the
    // transcript would read "conversation B" *and* "conversation A".
    await waitFor(() => expect(result.current.isHydrating).toBe(false))
    expect(result.current.messages.some((m) => m.content === "conversation A")).toBe(false)
    expect(result.current.messages.map((m) => m.id)).toEqual(["msg-b"])
  })

  it("does not let a superseded load clear the flag the current one needs", async () => {
    // The flag is turned off by whoever owns the transcript, and the failure mode
    // is in the *skip* direction: any load that finishes may clear it, so a slow
    // load for a conversation the user already left can blank the skeleton while
    // the conversation they are actually on is still fetching — an empty chat that
    // looks like the history load returned nothing.
    //
    // The converse is asserted too, because a flag that is never cleared is just
    // as broken (a permanent spinner) and a test that only checked the first half
    // would pass against that.
    let releaseA!: () => void
    let releaseB!: () => void
    const gateA = new Promise<void>((r) => {
      releaseA = r
    })
    const gateB = new Promise<void>((r) => {
      releaseB = r
    })
    let sawA = false
    let sawB = false
    server.use(
      http.get("/api/conversations/:id", async ({ params }) => {
        if (params.id === CONV) {
          sawA = true
          await gateA
          return HttpResponse.json({ id: CONV, system_prompt: null, messages: [] })
        }
        sawB = true
        await gateB
        return HttpResponse.json({ id: OTHER, system_prompt: null, messages: [] })
      })
    )

    const { result, rerender } = renderHook(
      ({ id }: { id: string }) => useNexusChat({ conversationId: id }),
      { initialProps: { id: CONV } }
    )
    await waitFor(() => expect(sawA).toBe(true))

    rerender({ id: OTHER })
    await waitFor(() => expect(sawB).toBe(true))

    // A finishes last, while B is still parked.
    await act(async () => {
      releaseA()
      await gateA
      // A timed flush rather than a `waitFor`: `waitFor` returns the *first* time
      // its condition holds, and the condition (the skeleton is up) already held
      // before A completed — so it would pass before the thing under test ran.
      // This is only "let the queued promises settle", not a wait for a condition.
      await new Promise((r) => setTimeout(r, 25))
    })

    expect(result.current.isHydrating).toBe(true)

    await act(async () => {
      releaseB()
      await gateB
      await new Promise((r) => setTimeout(r, 25))
    })
    expect(result.current.isHydrating).toBe(false)
  })
})