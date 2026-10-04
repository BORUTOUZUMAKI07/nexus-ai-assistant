/**
 * The rejoin half of the chat BFF: `GET /api/chat?runId=…`.
 *
 * A run is no longer the child of the request that started it, so the browser
 * can ask for a turn it was already watching. That makes this endpoint the
 * difference between "your answer was lost" and "here it is", and the failure
 * modes are the quiet kind:
 *
 *   - it forwards to the wrong path, and the answer never comes back;
 *   - it validates nothing, and a stale id in localStorage becomes a 500;
 *   - it re-translates the frames, and a second copy drifts from the first;
 *   - a 404 is swallowed into a generic error, so a run that no longer exists
 *     shows the user an error about their own conversation.
 *
 * Each of those is a test below. The "same translator" claim is the one that
 * cannot be asserted by reading either file, so it is checked by comparing the
 * output of the two entry points on one input.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { NextRequest } from "next/server"

// See chat-stream-translation.test.ts: `cookies()` reads Next's per-request
// async storage, which does not exist for a handler invoked directly. The token
// is not what these tests are about.
vi.mock("next/headers", () => ({
  cookies: async () => new Map<string, string>(),
}))

type Handler = (req: unknown, ctx: unknown) => Promise<Response>

const CONV = "22222222-2222-4222-8222-222222222222"
const RUN = "11111111-1111-4111-8111-111111111111"
// A UUID, because the route validates both ids before they reach the backend --
// a non-UUID here would be rejected at the edge and this test would pass for the
// wrong reason.
const MSG = "33333333-3333-4333-8333-333333333333"

/** Load the route fresh so the `server-only` import guard does not trip. */
async function chatRoute(): Promise<{ POST: Handler; GET: Handler }> {
  vi.resetModules()
  return (await import("@/app/api/chat/route")) as unknown as {
    POST: Handler
    GET: Handler
  }
}

/**
 * Stand in for the backend, capturing the request so the URL and headers can be
 * asserted. `status` and `body` are configurable because the interesting cases
 * are the failures, not only the happy path.
 */
function stubBackend(
  sse: string,
  init: { status?: number; headers?: Record<string, string> } = {}
) {
  const original = globalThis.fetch
  const seen: { url: string; init: RequestInit | undefined }[] = []
  globalThis.fetch = (async (input: RequestInfo | URL, reqInit?: RequestInit) => {
    seen.push({ url: String(input), init: reqInit })
    return new Response(sse, {
      status: init.status ?? 200,
      headers: init.headers ?? { "Content-Type": "text/event-stream" },
    })
  }) as unknown as typeof fetch
  return {
    seen,
    restore: () => {
      globalThis.fetch = original
    },
  }
}

function getReq(search = `?runId=${RUN}&conversationId=${CONV}`) {
  return new NextRequest(`http://localhost/api/chat${search}`, { method: "GET" })
}

async function frames(res: Response): Promise<string[]> {
  return (await res.text()).split("\n").filter((l) => l.length > 0)
}

describe("GET /api/chat — rejoin to a run already in flight", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("forwards to the run's own stream path", async () => {
    // The one thing that makes this endpoint different from the POST. A path
    // derived from the conversation id alone would silently return the *live*
    // stream for a fresh turn instead of the one being recovered, and the user
    // would watch a second answer arrive instead of the first one finishing.
    const stub = stubBackend('data: {"type":"done"}\n\n')
    try {
      const { GET } = await chatRoute()
      const res = await GET(getReq(), {})
      expect(res.status).toBe(200)
      expect(stub.seen[0].url).toContain(
        `/api/v1/conversations/${CONV}/runs/${RUN}/stream`
      )
      expect(stub.seen[0].init?.method).toBe("GET")
    } finally {
      stub.restore()
    }
  })

  it("translates a replayed run through the same dialect as a live one", async () => {
    // The claim that cannot be verified by reading either file: one translator,
    // two entry points. If a second copy exists and drifts, this is where it
    // shows. Both entry points get byte-identical backend input and their
    // outputs are compared, so a divergence fails even if both outputs are
    // individually plausible.
    const sse =
      'data: {"type":"text_delta","content":"half "}\n\n' +
      'data: {"type":"citation","source":"docs","snippet":"s"}\n\n' +
      'data: {"type":"done","thread_id":"t-1"}\n\n'
    const stub = stubBackend(sse)
    try {
      const { GET, POST } = await chatRoute()
      const replayed = await frames(
        await GET(getReq(), {})
      )
      stub.restore()
      const stub2 = stubBackend(sse)
      const live = await frames(
        await POST(
          new NextRequest("http://localhost/api/chat", {
            method: "POST",
            body: JSON.stringify({
              messages: [{ role: "user", content: "hi" }],
              conversationId: CONV,
            }),
          }),
          {}
        )
      )
      stub2.restore()

      expect(replayed).toEqual(live)
      expect(replayed.length).toBeGreaterThan(0)
      // Spot-check that the shared translator is actually the one producing
      // them, so the equality cannot pass by both sides emitting nothing.
      expect(replayed[0]).toBe(`0:${JSON.stringify("half ")}`)
      expect(replayed.some((l) => l.startsWith("8:"))).toBe(true)
    } finally {
      stub.restore()
    }
  })

  it("forwards Last-Event-ID only when the client has a cursor", async () => {
    // Sending a stale cursor silently drops the front of the answer, and
    // sending `Last-Event-ID: 0` invites exactly that. An absent header is
    // easier to reason about than "0 means everything".
    const stub = stubBackend("")
    try {
      const { GET } = await chatRoute()

      await GET(getReq(), {})
      expect(new Headers(stub.seen[0].init?.headers).get("Last-Event-ID")).toBeNull()

      await GET(getReq(`?runId=${RUN}&conversationId=${CONV}&lastEventId=42`), {})
      expect(new Headers(stub.seen[1].init?.headers).get("Last-Event-ID")).toBe("42")
    } finally {
      stub.restore()
    }
  })

  it("rejects a non-UUID target before it reaches the backend", async () => {
    // A stale or hand-edited localStorage entry must not become a request. The
    // 400 is the useful part; not forwarding is the part that keeps a garbage
    // id from being reported as a backend failure.
    const stub = stubBackend("")
    try {
      const { GET } = await chatRoute()
      for (const search of [
        "?runId=not-a-uuid&conversationId=" + CONV,
        "?runId=" + RUN + "&conversationId=nope",
        "",
      ]) {
        const res = await GET(getReq(search), {})
        expect(res.status).toBe(400)
        expect(await res.json()).toEqual({ detail: "invalid_rejoin_target" })
      }
      expect(stub.seen).toHaveLength(0)
    } finally {
      stub.restore()
    }
  })

  it("reports a missing run as a 404 the client can act on", async () => {
    // Distinct from every other backend failure on purpose. "That run is gone"
    // means the client should forget the id and stop asking; collapsing it into
    // `backend_rejected_404` leaves it retrying forever and, worse, shows the
    // user an error about their own conversation.
    const stub = stubBackend(JSON.stringify({ detail: "run_not_found" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    })
    try {
      const { GET } = await chatRoute()
      const res = await GET(getReq(), {})
      expect(res.status).toBe(404)
      expect(await res.json()).toEqual({ detail: "run_not_found" })
    } finally {
      stub.restore()
    }
  })

  it("echoes the run id back so a client can chain onto the same run", async () => {
    // The rejoin response is consumed by code that may then need to re-attach
    // again (a second reload mid-recovery). Preferring the backend's value and
    // falling back to the requested one means the header is never blank, which
    // would read as "this run cannot be resumed".
    const stub = stubBackend("", { headers: { "X-Nexus-Run-Id": RUN } })
    try {
      const { GET } = await chatRoute()
      expect((await GET(getReq(), {})).headers.get("X-Nexus-Run-Id")).toBe(RUN)
      stub.restore()

      const bare = stubBackend("")
      const res = await (await chatRoute()).GET(getReq(), {})
      expect(res.headers.get("X-Nexus-Run-Id")).toBe(RUN)
      bare.restore()
    } finally {
      stub.restore()
    }
  })

  it("forwards the persisted-message id so the client can skip a duplicate", async () => {
    // The header crosses the proxy. If the BFF drops it, the client falls back to
    // rendering the replay -- which is right for a live run and wrong for a
    // finished one, so the failure mode is "the user reads the same answer twice"
    // with no error anywhere. Not a new SSE frame type for exactly that reason:
    // a frame the translator does not know is dropped in silence (AGENTS.md §2).
    const stub = stubBackend("", {
      headers: { "X-Nexus-Run-Id": RUN, "X-Nexus-Message-Id": MSG },
    })
    try {
      const { GET } = await chatRoute()
      const res = await GET(getReq(), {})
      expect(res.headers.get("X-Nexus-Message-Id")).toBe(MSG)
    } finally {
      stub.restore()
    }
  })

  it("sends no message id when the backend has none to send", async () => {
    // A run still producing has no row. A blank header would be read by the
    // client as an id that matches nothing, which happens to be harmless -- and
    // a placeholder would not be, so the absence has to stay an absence.
    const stub = stubBackend("", { headers: { "X-Nexus-Run-Id": RUN } })
    try {
      const { GET } = await chatRoute()
      const res = await GET(getReq(), {})
      expect(res.headers.has("X-Nexus-Message-Id")).toBe(false)
    } finally {
      stub.restore()
    }
  })

  it("reports a backend failure as that backend's own status", async () => {
    // A 401 here is the one case worth retrying after a token refresh; turning
    // it into a 200 with an error frame would hide it, which is the bug the
    // POST handler was fixed for.
    const stub = stubBackend(JSON.stringify({ detail: "unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    })
    try {
      const { GET } = await chatRoute()
      expect((await GET(getReq(), {})).status).toBe(401)
    } finally {
      stub.restore()
    }
  })

  it("never sends a request body", async () => {
    // The sweep in route-handlers.test.ts asserts this for helper-based routes;
    // asserted here as well because a GET with a body is the kind of thing that
    // passes a proxy and is then rejected by the real backend.
    const stub = stubBackend("")
    try {
      const { GET } = await chatRoute()
      await GET(getReq(), {})
      expect(stub.seen[0].init?.body).toBeUndefined()
    } finally {
      stub.restore()
    }
  })
})