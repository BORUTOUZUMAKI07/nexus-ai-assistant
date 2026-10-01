/**
 * The chat BFF route translates the backend's `data: {json}` SSE dialect into
 * the Vercel AI SDK 3 data-stream dialect the browser hook actually reads.
 *
 * That translation is a real protocol boundary, and it is the one place a
 * backend event can be added and silently never reach the user. The backend
 * emitting an event nobody translates looks exactly like the backend not
 * emitting it, so each event type gets a test asserting the frame that comes
 * out the other side.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { NextRequest } from "next/server"

// `cookies()` reads Next's per-request async storage, which does not exist when
// a handler is invoked directly instead of by the framework. The route only
// uses it to forward the session bearer token, which is not what these tests
// are about, so an empty store is the honest stub. Without it the handler
// throws before reaching a single line of translation logic.
vi.mock("next/headers", () => ({
  cookies: async () => new Map<string, string>(),
}))

type Handler = (req: unknown, ctx: unknown) => Promise<Response>

/** Load the route fresh so the `server-only` import guard does not trip. */
async function chatRoute(): Promise<Handler> {
  vi.resetModules()
  const mod = (await import("@/app/api/chat/route")) as unknown as {
    POST: Handler
  }
  return mod.POST
}

/**
 * Stand in for the backend stream.
 *
 * `body` is emitted as one chunk, which is the easy case; the route's own
 * buffering is covered by the hook tests. What is under test here is only the
 * event-by-event translation.
 */
function stubBackend(sse: string) {
  const original = globalThis.fetch
  globalThis.fetch = (async () =>
    new Response(sse, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    })) as unknown as typeof fetch
  return () => {
    globalThis.fetch = original
  }
}

function request() {
  return new NextRequest("http://localhost/api/chat", {
    method: "POST",
    body: JSON.stringify({
      messages: [{ role: "user", content: "write me a report" }],
      conversationId: "11111111-1111-1111-1111-111111111111",
    }),
  })
}

/** Read a streaming response to completion and split it into frames. */
async function frames(res: Response): Promise<string[]> {
  const body = await res.text()
  return body.split("\n").filter((l) => l.length > 0)
}

/** The decoded payload of the single `8:` annotation frame in a response. */
async function annotation(res: Response): Promise<{ type: string; data: unknown }> {
  const found = (await frames(res)).filter((l) => l.startsWith("8:"))
  expect(found.length).toBe(1)
  return JSON.parse(found[0].slice(2))[0]
}

describe("/api/chat SSE translation", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("translates a backend artifact event into an artifact annotation", async () => {
    // The whole point of the feature. The backend persists a document and says
    // so; if this translation is missing the document exists in the database and
    // the canvas never opens, and no test anywhere else would notice.
    const restore = stubBackend(
      'data: {"type":"artifact","artifact_id":"art-9","title":"Q3 Report","version":2,"created":false}\n\n'
    )
    try {
      const POST = await chatRoute()
      const res = await POST(request(), {})
      const ann = await annotation(res)
      expect(ann.type).toBe("artifact")
      expect(ann.data).toEqual({
        artifact_id: "art-9",
        title: "Q3 Report",
        version: 2,
        created: false,
      })
    } finally {
      restore()
    }
  })

  it("forwards only the id, never the document body", async () => {
    // A 120k-character artifact arriving in an SSE frame would be buffered in
    // the browser and again in the canvas, for no benefit: the canvas fetches
    // the content itself. If `content` ever appears here, this fails.
    const restore = stubBackend(
      'data: {"type":"artifact","artifact_id":"art-9","title":"T","version":1,"created":true,"content":"SECRET BODY"}\n\n'
    )
    try {
      const POST = await chatRoute()
      const res = await POST(request(), {})
      const text = await res.text()
      expect(text).not.toContain("SECRET BODY")
    } finally {
      restore()
    }
  })

  it("defaults a missing title, version and created flag", async () => {
    // The backend always sends them, but a default here means a future backend
    // change that omits one produces an openable canvas rather than a canvas
    // titled "undefined".
    const restore = stubBackend(
      'data: {"type":"artifact","artifact_id":"art-9"}\n\n'
    )
    try {
      const POST = await chatRoute()
      const res = await POST(request(), {})
      const ann = await annotation(res)
      expect(ann.data).toEqual({
        artifact_id: "art-9",
        title: "",
        version: 1,
        created: true,
      })
    } finally {
      restore()
    }
  })

  it("still translates text deltas and the done event alongside an artifact", async () => {
    // Guards the `else if` chain: a new branch must not have displaced an
    // existing one, and the artifact frame must arrive after the text so the
    // canvas opens over a complete answer.
    const restore = stubBackend(
      'data: {"type":"text_delta","content":"Here it is."}\n\n' +
        'data: {"type":"artifact","artifact_id":"art-1","title":"T","version":1,"created":true}\n\n' +
        'data: {"type":"done","content":"Here it is.","thread_id":"t-1"}\n\n'
    )
    try {
      const POST = await chatRoute()
      const res = await POST(request(), {})
      const all = await frames(res)
      const textIdx = all.findIndex((l) => l.startsWith("0:"))
      const artifactIdx = all.findIndex((l) => l.includes('"artifact"'))
      const createdIdx = all.findIndex((l) => l.includes("conversation_created"))
      expect(textIdx).toBeGreaterThanOrEqual(0)
      expect(artifactIdx).toBeGreaterThan(textIdx)
      expect(createdIdx).toBeGreaterThan(artifactIdx)
    } finally {
      restore()
    }
  })

  it("leaves critique and quality events untranslated", async () => {
    // They are dropped today, and this is here so that stays a decision rather
    // than an accident: if someone wires one up, this test fails and makes them
    // also handle it in the hook.
    const restore = stubBackend(
      'data: {"type":"critique","critique":"too short"}\n\n' +
        'data: {"type":"quality","evidence_score":0.4}\n\n'
    )
    try {
      const POST = await chatRoute()
      const res = await POST(request(), {})
      const all = await frames(res)
      expect(all.filter((l) => l.startsWith("8:")).length).toBe(0)
    } finally {
      restore()
    }
  })
})
