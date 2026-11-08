/**
 * The four per-message actions a chat transcript offers: delete, edit-and-resend,
 * regenerate, branch.
 *
 * These are the hook halves of the feature -- the server-side cascade and
 * ordering rules are pinned in `backend/tests/test_message_edit_delete.py`, and
 * the buttons are pinned in the ChatArea component tests. What is *only* here is
 * the seam between them: which id shape reaches the server, what local list the
 * hook builds before it resends, and what it does with the *stored* prompt a
 * regenerate returns.
 *
 * The id shape is the load-bearing part and the reason this file exists. A live
 * turn carries a client id (`msg-<ts>`) and cannot be addressed by the message
 * endpoints; a hydrated or rejoined turn carries the server's UUID. The two are
 * otherwise indistinguishable at the call site, so a hook that guessed from list
 * position would address the wrong row after an edit-and-resend (which leaves a
 * live turn sitting above hydrated ones). Every test here that expects a server
 * call seeds a real UUID through `serveHistory` and every test that expects a
 * *local-only* path seeds a `msg-` id.
 *
 * Run with `node node_modules/vitest/vitest.mjs run` on Windows (the bin shim
 * is broken -- See project guidelines.
 */
import { describe, it, expect, beforeEach } from "vitest"
import { renderHook, act, waitFor } from "@/test/test-utils"
import { useNexusChat } from "@/hooks/useNexusChat"
import type { Conversation } from "@/lib/api"
import { server } from "@/test/mocks/server"
import { chatStreamBody } from "@/test/mocks/handlers"
import { http, HttpResponse } from "msw"

const CONV = "11111111-1111-4111-8111-111111111111"
const USER_MSG = "22222222-2222-4222-8222-222222222222"
const ASSISTANT_MSG = "33333333-3333-4333-8333-333333333333"

/** A stored conversation row in the shape `messagesFromHistory` reads. */
function row(over: Record<string, unknown> = {}) {
  return {
    id: USER_MSG,
    role: "user",
    content: "what is the capital of France",
    thought_process: null,
    model: null,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    citations: [],
    tool_calls: [],
    user_feedback: null,
    created_at: "2026-10-21T00:00:00Z",
    ...over,
  }
}

/** Serve a fixed transcript for `CONV`. */
function serveHistory(messages: unknown[]) {
  server.use(
    http.get("/api/conversations/:id", () =>
      HttpResponse.json({ id: CONV, title: "t", system_prompt: null, messages })
    )
  )
}

/** A two-turn transcript with real server UUIDs. */
function serveTwoTurns() {
  serveHistory([
    row({ id: USER_MSG, role: "user", content: "what is the capital of France" }),
    row({
      id: ASSISTANT_MSG,
      role: "assistant",
      content: "Paris",
      created_at: "2026-10-21T00:00:01Z",
    }),
  ])
}

/** Wait for the hook to have hydrated the served transcript. */
async function hydrated(result: { current: ReturnType<typeof useNexusChat> }) {
  await waitFor(() => expect(result.current.messages.length).toBeGreaterThan(0))
  return result.current.messages
}

describe("per-message actions", () => {
  beforeEach(() => {
    window.localStorage.clear()
    server.use(http.get("/api/chat", () => new HttpResponse("")))
  })

  // ── delete ──────────────────────────────────────────────────────────────

  it("deletes a hydrated turn through the server and re-hydrates the list", async () => {
    serveTwoTurns()
    let deleteCalls = 0
    server.use(
      http.delete("/api/conversations/:id/messages/:messageId", () => {
        deleteCalls += 1
        return HttpResponse.json({ deleted: 2 })
      })
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await hydrated(result)

    // Only *after* the transcript is hydrated does the conversation read come
    // back empty -- which is what the cascade would leave and what the hook must
    // display. Serving the empty body from the start would make `hydrated()`
    // wait forever and the test would time out rather than fail.
    let historyCalls = 0
    server.use(
      http.get("/api/conversations/:id", () => {
        historyCalls += 1
        return HttpResponse.json({ id: CONV, title: "t", system_prompt: null, messages: [] })
      })
    )

    await act(async () => {
      await result.current.removeMessage(ASSISTANT_MSG)
    })

    expect(deleteCalls).toBe(1)
    // Re-hydrated rather than sliced: the cascade count is not trusted.
    expect(historyCalls).toBeGreaterThan(0)
    await waitFor(() => expect(result.current.messages).toEqual([]))
  })

  it("removes a live turn locally without calling the server", async () => {
    serveTwoTurns()
    let deleteCalls = 0
    server.use(
      http.delete("/api/conversations/:id/messages/:messageId", () => {
        deleteCalls += 1
        return HttpResponse.json({ deleted: 1 })
      })
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await hydrated(result)

    act(() => {
      result.current.setMessages((prev) => [
        ...prev,
        { id: "msg-1700000000000", role: "assistant", content: "streaming answer" },
      ])
    })

    await act(async () => {
      await result.current.removeMessage("msg-1700000000000")
    })

    // A client id was never persisted, so there is nothing to delete server-side
    // and the bubble simply goes away.
    expect(deleteCalls).toBe(0)
    expect(result.current.messages.some((m) => m.id === "msg-1700000000000")).toBe(false)
  })

  it("records the error and rethrows when the delete fails", async () => {
    serveTwoTurns()
    server.use(
      http.delete("/api/conversations/:id/messages/:messageId", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 })
      )
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await hydrated(result)

    await act(async () => {
      await expect(result.current.removeMessage(ASSISTANT_MSG)).rejects.toThrow()
    })
    // The hook keeps the caller's stack intact (rethrow) *and* surfaces it, so
    // a caller that awaits can roll back while the banner still shows.
    await waitFor(() => expect(result.current.error).not.toBeNull())
  })

  it("clears the turn verdict when the turn it describes is deleted", async () => {
    // The verdict has no message id to attach to (the backend emits it after
    // the row exists), so it lives in its own state -- which means nothing ties
    // its lifetime to the messages. Deleting the turn left the badge behind,
    // floating over whatever the transcript showed next, including the empty
    // welcome screen. The cascade takes every later turn, so a delete can
    // always remove the answer the verdict describes.
    serveTwoTurns()
    server.use(
      http.post("/api/chat", () =>
        new HttpResponse(
          chatStreamBody(["Paris."], [
            { type: "critique", data: { critique: "too thin", revision_count: 2 } },
            { type: "quality", data: { evidence_score: 0.42, evidence_gate_passed: false } },
          ]),
          { headers: { "Content-Type": "text/plain; charset=utf-8" } }
        )
      ),
      http.delete("/api/conversations/:id/messages/:messageId", () =>
        HttpResponse.json({ deleted: 3 })
      )
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await hydrated(result)

    await act(async () => {
      await result.current.sendMessage("summarise")
    })
    await waitFor(() => expect(result.current.turnVerdict).not.toBeNull())

    // After the cascade the conversation is empty -- and the badge must go with it.
    server.use(
      http.get("/api/conversations/:id", () =>
        HttpResponse.json({ id: CONV, title: "t", system_prompt: null, messages: [] })
      )
    )

    await act(async () => {
      await result.current.removeMessage(USER_MSG)
    })

    await waitFor(() => expect(result.current.messages).toEqual([]))
    expect(result.current.turnVerdict).toBeNull()
  })

  // ── edit and resend ─────────────────────────────────────────────────────

  it("patches the server, truncates locally to the edited turn, and resends", async () => {
    serveTwoTurns()
    let patched: { id: string; body: unknown } | null = null
    let resent: string | null = null
    let reuseId: unknown
    let bodyMessages: Array<{ role: string; content: string }> = []
    server.use(
      http.patch("/api/conversations/:id/messages/:messageId", async ({ request, params }) => {
        patched = { id: String(params.messageId), body: await request.json() }
        return HttpResponse.json(row({ id: USER_MSG, content: "what is the capital of Italy" }))
      }),
      http.post("/api/chat", async ({ request }) => {
        // Capture the last user turn the resend actually carried.
        const body = (await request.json()) as {
          messages?: Array<{ role: string; content: string }>
          reuseUserMessageId?: unknown
        }
        bodyMessages = body.messages ?? []
        resent = body.messages?.filter((m) => m.role === "user").pop()?.content ?? null
        reuseId = body.reuseUserMessageId
        return new HttpResponse(chatStreamBody(["Rome"]), {
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        })
      })
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await hydrated(result)

    await act(async () => {
      await result.current.editAndResend(USER_MSG, "what is the capital of Italy")
    })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    expect(patched).not.toBeNull()
    expect(patched!.id).toBe(USER_MSG)
    // `MessageEdit` is extra="forbid", so the body must be exactly {content}.
    expect(patched!.body).toEqual({ content: "what is the capital of Italy" })
    // The stale "Paris" reply is gone: the transcript was truncated to the edit
    // before the resend, so the model never sees an answer to the old question.
    expect(result.current.messages.some((m) => m.content === "Paris")).toBe(false)
    expect(resent).toBe("what is the capital of Italy")
    // The edited row still exists server-side, so the resend must name it or the
    // stream route appends a second copy of the question (the duplicate-turn
    // defect measured live).
    expect(reuseId).toBe(USER_MSG)
    // Exactly one *user* turn reaches the wire. `reuseUserMessageId` marks the
    // edited row as already-persisted, so the optimistic bubble must not also be
    // counted as a user turn — otherwise the backend replays the question twice.
    const userTurnsInPayload = bodyMessages.filter((m) => m.role === "user")
    expect(userTurnsInPayload).toHaveLength(1)
  })

  it("adopts the persisted row ids after a completed turn, so the first edit reaches the server", async () => {
    // A freshly-sent turn IS persisted by the backend, but the POST stream only
    // reports the run id — the client keeps its own `msg-…` ids until something
    // reads the history back. Every message action keys off a server UUID, so
    // without adoption the *first* edit on a fresh turn skips the PATCH
    // entirely (isServerId fails), the resend runs anyway, and the database
    // keeps the old turn the browser just discarded: "later turns will be
    // discarded" would hold only in the UI.
    const ASK = "what is the capital of France"
    const ANSWER = "Paris."
    let persisted = false
    let patchCalls = 0
    server.use(
      http.get("/api/conversations/:id", () => {
        // Empty until the run has written its rows — which is the state both
        // the mount read and a lagging read would see.
        const messages = persisted
          ? [
              row({ id: USER_MSG, role: "user", content: ASK }),
              row({
                id: ASSISTANT_MSG,
                role: "assistant",
                content: ANSWER,
                created_at: "2026-10-21T00:00:01Z",
              }),
            ]
          : []
        return HttpResponse.json({ id: CONV, title: "t", system_prompt: null, messages })
      }),
      http.post("/api/chat", () => {
        persisted = true
        return new HttpResponse(chatStreamBody([ANSWER]), {
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        })
      }),
      http.patch("/api/conversations/:id/messages/:messageId", () => {
        patchCalls += 1
        return HttpResponse.json(row({ id: USER_MSG, content: "edited" }))
      })
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await waitFor(() => expect(result.current.messages).toEqual([]))

    await act(async () => {
      await result.current.sendMessage(ASK)
    })

    // The turn now carries the server's ids rather than `msg-…` ones.
    await waitFor(() =>
      expect(result.current.messages.map((m) => m.id)).toEqual([USER_MSG, ASSISTANT_MSG])
    )

    // …so the very first edit is addressed to the server instead of skipped.
    await act(async () => {
      await result.current.editAndResend(USER_MSG, "edited")
    })
    expect(patchCalls).toBe(1)
  })

  it("keeps the streamed partial when the run errored, and does not read history back", async () => {
    // Adoption runs only for a run that finished cleanly. On a `3:` error the
    // answer was never persisted, so reading history back would replace the
    // partial text the user can still read with whatever the server held before
    // the turn started — and the read itself would be a lie: there are no new
    // rows to adopt ids from.
    let reads = 0
    server.use(
      http.get("/api/conversations/:id", () => {
        reads += 1
        return HttpResponse.json({
          id: CONV,
          title: "t",
          system_prompt: null,
          messages: [row({ id: USER_MSG, role: "user", content: "an older turn" })],
        })
      }),
      http.post(
        "/api/chat",
        () =>
          new HttpResponse(
            `0:${JSON.stringify("partial answer")}\n3:${JSON.stringify("boom")}\n`,
            { headers: { "Content-Type": "text/plain; charset=utf-8" } }
          )
      )
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await waitFor(() => expect(result.current.messages).toEqual([]))

    await act(async () => {
      await result.current.sendMessage("ask")
    })

    expect(result.current.messages.some((m) => m.content === "partial answer")).toBe(true)
    // Exactly one history read: the mount's. The error path attempted no adoption.
    expect(reads).toBe(1)
  })

  it("does nothing for an empty edit", async () => {
    serveTwoTurns()
    let patched = 0
    server.use(
      http.patch("/api/conversations/:id/messages/:messageId", () => {
        patched += 1
        return HttpResponse.json(row())
      })
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await hydrated(result)

    await act(async () => {
      await result.current.editAndResend(USER_MSG, "   ")
    })

    expect(patched).toBe(0)
  })

  it("does not call the server for a live turn's edit", async () => {
    serveTwoTurns()
    let patched = 0
    let reuseId: unknown
    server.use(
      http.patch("/api/conversations/:id/messages/:messageId", () => {
        patched += 1
        return HttpResponse.json(row())
      }),
      http.post("/api/chat", async ({ request }) => {
        const body = (await request.json()) as { reuseUserMessageId?: unknown }
        reuseId = body.reuseUserMessageId
        return new HttpResponse(chatStreamBody(["ok"]), {
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        })
      })
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await hydrated(result)
    act(() => {
      result.current.setMessages((prev) => [
        ...prev,
        { id: "msg-1700000000001", role: "user", content: "old" },
      ])
    })

    await act(async () => {
      await result.current.editAndResend("msg-1700000000001", "new")
    })

    expect(patched).toBe(0)
    // A client id was never stored, so there is no row to adopt.
    expect(reuseId).toBeUndefined()
  })

  it("does not duplicate the question when re-sending an edit of a live turn", async () => {
    serveTwoTurns()
    let payloadUserTurns: string[] = []
    server.use(
      http.post("/api/chat", async ({ request }) => {
        const body = (await request.json()) as {
          messages?: Array<{ role: string; content: string }>
        }
        payloadUserTurns = (body.messages ?? [])
          .filter((m) => m.role === "user")
          .map((m) => m.content)
        return new HttpResponse(chatStreamBody(["ok"]), {
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        })
      })
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await hydrated(result)
    act(() => {
      result.current.setMessages((prev) => [
        ...prev,
        { id: "msg-1700000000010", role: "user", content: "draft question" },
      ])
    })

    await act(async () => {
      await result.current.editAndResend("msg-1700000000010", "final question")
    })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    // The edited turn is already the last row in the list; appending a second
    // optimistic bubble rendered the question twice beside the new answer and
    // put it in the resend payload twice (measured live 2026-10-24). Exactly
    // one copy of the edited text may exist in each.
    const shown = result.current.messages.filter(
      (m) => m.role === "user" && m.content === "final question"
    )
    expect(shown).toHaveLength(1)
    expect(payloadUserTurns.filter((t) => t === "final question")).toHaveLength(1)
    // And the stale text is gone, not stacked under the new one.
    expect(result.current.messages.some((m) => m.content === "draft question")).toBe(false)
  })

  // ── regenerate ──────────────────────────────────────────────────────────

  it("regenerates from the *stored* prompt the server returns, not the client copy", async () => {
    serveTwoTurns()
    let resent: string | null = null
    let reuseId: unknown
    let regenerateCalls = 0
    server.use(
      http.post("/api/conversations/:id/messages/:messageId/regenerate", () => {
        regenerateCalls += 1
        // Deliberately different from the hydrated user turn: a hook that
        // resent its own copy would send the row's text, and only a hook that
        // uses the server's return value sends this.
        return HttpResponse.json({
          prompt: "STORED-PROMPT-FROM-SERVER",
          user_message_id: USER_MSG,
        })
      }),
      http.post("/api/chat", async ({ request }) => {
        const body = (await request.json()) as {
          messages?: Array<{ role: string; content: string }>
          reuseUserMessageId?: unknown
        }
        resent = body.messages?.filter((m) => m.role === "user").pop()?.content ?? null
        reuseId = body.reuseUserMessageId
        return new HttpResponse(chatStreamBody(["a fresh answer"]), {
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        })
      })
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await hydrated(result)

    await act(async () => {
      await result.current.regenerateMessage(ASSISTANT_MSG)
    })
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    expect(regenerateCalls).toBe(1)
    expect(resent).toBe("STORED-PROMPT-FROM-SERVER")
    // The endpoint's id is what makes the resend adopt the stored prompt row
    // instead of appending a duplicate.
    expect(reuseId).toBe(USER_MSG)
    expect(result.current.messages.some((m) => m.content === "Paris")).toBe(false)
  })

  it("falls back to a local reload for a live (unpersisted) reply", async () => {
    serveTwoTurns()
    let regenerateCalls = 0
    server.use(
      http.post("/api/conversations/:id/messages/:messageId/regenerate", () => {
        regenerateCalls += 1
        return HttpResponse.json({ prompt: "x" })
      })
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await hydrated(result)
    act(() => {
      result.current.setMessages((prev) => [
        ...prev,
        { id: "msg-1700000000002", role: "assistant", content: "live reply" },
      ])
    })

    await act(async () => {
      await result.current.regenerateMessage("msg-1700000000002")
    })

    // Nothing was persisted, so there is no reply to delete server-side.
    expect(regenerateCalls).toBe(0)
  })

  it("records the error and rethrows when regenerate fails", async () => {
    serveTwoTurns()
    server.use(
      http.post("/api/conversations/:id/messages/:messageId/regenerate", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 })
      )
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await hydrated(result)

    await act(async () => {
      await expect(result.current.regenerateMessage(ASSISTANT_MSG)).rejects.toThrow()
    })
    await waitFor(() => expect(result.current.error).not.toBeNull())
  })

  // ── branch ──────────────────────────────────────────────────────────────

  it("forks the conversation and returns the created conversation for a server turn", async () => {
    serveTwoTurns()
    let forkBody: Record<string, unknown> | null = null
    server.use(
      http.post("/api/conversations/:id/fork", async ({ request }) => {
        forkBody = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({
          ...row(),
          id: "conv-forked-1",
          title: "Live chat-feature verification (Branch: Forked Branch)",
        })
      })
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await hydrated(result)

    let forked: Conversation | null = null
    await act(async () => {
      forked = await result.current.branchFromMessage(ASSISTANT_MSG)
    })

    expect(forked!.id).toBe("conv-forked-1")
    // The whole object travels, not just the id: the caller seeds its
    // conversation list from it, and a fork the list cannot name is a fork the
    // auto-titler renames (see the page-level guard).
    expect(forked!.title).toBe("Live chat-feature verification (Branch: Forked Branch)")
    // A server id is a real fork point and travels in the body.
    expect(forkBody!.fork_message_id).toBe(ASSISTANT_MSG)
  })

  it("omits the fork point for a live turn (nothing to address yet)", async () => {
    serveTwoTurns()
    let forkBody: Record<string, unknown> | null = null
    server.use(
      http.post("/api/conversations/:id/fork", async ({ request }) => {
        forkBody = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ ...row(), id: "conv-forked-2" })
      })
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await hydrated(result)

    await act(async () => {
      await result.current.branchFromMessage("msg-1700000000003")
    })

    // `forkConversation` only forwards a fork point that is a real UUID, so a
    // client id must not reach the body.
    expect(forkBody!.fork_message_id).toBeUndefined()
  })

  it("returns null and does not fork without a conversation", async () => {
    let forkCalls = 0
    server.use(
      http.post("/api/conversations/:id/fork", () => {
        forkCalls += 1
        return HttpResponse.json(row())
      })
    )

    const { result } = renderHook(() => useNexusChat())
    let forked: Conversation | null | string = "sentinel"
    await act(async () => {
      forked = await result.current.branchFromMessage(ASSISTANT_MSG)
    })

    expect(forked).toBeNull()
    expect(forkCalls).toBe(0)
  })

  it("records the error and rethrows when the fork fails", async () => {
    serveTwoTurns()
    server.use(
      http.post("/api/conversations/:id/fork", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 })
      )
    )

    const { result } = renderHook(() => useNexusChat({ conversationId: CONV }))
    await hydrated(result)

    await act(async () => {
      await expect(result.current.branchFromMessage(ASSISTANT_MSG)).rejects.toThrow()
    })
    await waitFor(() => expect(result.current.error).not.toBeNull())
  })
})
