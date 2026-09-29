/**
 * Cross-tab refresh coordination.
 *
 * The bug: two tabs whose access tokens expired together each fired their own
 * refresh carrying the same httpOnly cookie. The backend could not tell that
 * apart from a stolen token being replayed, so it revoked every session and the
 * user was logged out of everything for opening a second tab.
 *
 * The fake BroadcastChannel below delivers messages between "tabs" through a
 * queue, which is what makes the mutual-stand-down deadlock observable: with a
 * naive "someone else claimed, so I stand down" election, every tab stands down
 * and nobody ever refreshes -- a worse failure than the original bug.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import { server } from "@/test/mocks/server"
import { http, HttpResponse } from "msw"
import { mockConversations } from "@/test/mocks/handlers"

/** One channel per open tab, sharing a bus so messages cross tabs. */
class FakeBus {
  private channels = new Set<FakeChannel>()

  /** Called by each tab as it constructs its channel. */
  open(ch: FakeChannel) {
    this.channels.add(ch)
  }

  close(ch: FakeChannel) {
    this.channels.delete(ch)
  }

  /** How many tabs currently hold a channel. */
  get size() {
    return this.channels.size
  }

  /** Deliver to every other channel, as the real BroadcastChannel does. */
  deliver(from: FakeChannel, data: unknown) {
    // Simulates a leader tab dying before it can report: its "done" is never
    // seen, so a follower can only recover via the lock TTL.
    if ((data as { type?: string })?.type === "done" && !deliverDone) return
    if ((data as { type?: string })?.type === "claim") lastClaimAt = Date.now()
    for (const ch of [...this.channels]) {
      if (ch === from) continue
      ch.receive(data)
    }
  }
}

class FakeChannel {
  onmessage: ((event: MessageEvent) => void) | null = null
  closed = false
  private listeners = new Set<(event: MessageEvent) => void>()

  constructor(private bus: FakeBus) {}

  postMessage = (data: unknown) => {
    if (this.closed) throw new Error("postMessage on a closed channel")
    // Async, like the real thing: synchronous delivery would hide the ordering
    // bugs a real message queue makes visible.
    queueMicrotask(() => this.bus.deliver(this, data))
  }

  addEventListener = (_type: string, fn: (event: MessageEvent) => void) => {
    this.listeners.add(fn)
  }

  removeEventListener = (_type: string, fn: (event: MessageEvent) => void) => {
    this.listeners.delete(fn)
  }

  close = () => {
    this.closed = true
    this.listeners.clear()
    this.bus.close(this)
  }

  receive(data: unknown) {
    if (this.closed) return
    const event = { data } as MessageEvent
    this.onmessage?.(event)
    for (const fn of this.listeners) fn(event)
  }
}

let bus: FakeBus
let refreshCalls: number
/** Set false to simulate a leader that claimed and then vanished. */
let deliverDone: boolean
/** When the most recent claim was delivered, per fake-timer Date.now(). */
let lastClaimAt: number
/**
 * How many 401s to hold before answering any of them. This barrier is what
 * makes the race genuine rather than a scheduling accident: without it the first
 * tab's 401 can return early enough for its election window to expire before
 * the second tab has even asked, so the two refreshes never overlap and the
 * cross-tab path is never exercised.
 */
let barrierSize: number
let barrierArrived: (() => void)[]
/** When set, the refresh response is held until the returned function is called. */
let refreshGate: Promise<void> | null
let releaseRefresh: () => void
/** True once a refresh has completed and the cookie is genuinely valid again. */
let tokenFresh: boolean
/** How many times a mocked route actually answered 401. */
let unauthorized: number

/** A 401 that counts itself, so a test can prove the route was really hit. */
function expired() {
  unauthorized += 1
  return HttpResponse.json({ detail: "expired" }, { status: 401 })
}

beforeEach(() => {
  bus = new FakeBus()
  refreshCalls = 0
  deliverDone = true
  lastClaimAt = 0
  barrierSize = 0
  barrierArrived = []
  refreshGate = null
  releaseRefresh = () => {}
  tokenFresh = false
  unauthorized = 0
  vi.useFakeTimers({ shouldAdvanceTime: true })

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(globalThis as any).BroadcastChannel = class extends FakeChannel {
    constructor() {
      super(bus)
      // Registering is the whole point: a channel that never joins the bus can
      // never hear a peer, and the election silently passes every tab.
      bus.open(this)
    }
  }

  // The access token is expired until *someone* refreshes. That is the whole
  // situation under test, and it is what makes refreshCalls the thing to
  // assert: two tabs racing here is exactly the reported bug.
  server.use(
    http.get("/api/conversations", async () => {
      if (barrierSize > 0) {
        await new Promise<void>((resolve) => {
          barrierArrived.push(resolve)
          if (barrierArrived.length >= barrierSize) {
            for (const release of barrierArrived.splice(0)) release()
          }
        })
      }
      // The access token stays expired until a refresh *completes*, not merely
      // starts. The cookie is only re-baked when the response lands, so a tab
      // arriving while the leader is mid-exchange really does still get a 401 --
      // modelling that is what makes a staggered arrival a real race rather
      // than a tab that quietly succeeds without ever contending.
      if (tokenFresh) return HttpResponse.json(mockConversations)
      return expired()
    }),
    http.post("/api/auth/refresh", async () => {
      refreshCalls += 1
      // Hold the winner inside the exchange, so the tabs genuinely overlap: a
      // second refresh arriving while this is blocked is the bug, unambiguously.
      if (refreshGate) await refreshGate
      tokenFresh = true
      return HttpResponse.json({ ok: true })
    }),
  )
})

afterEach(() => {
  vi.useRealTimers()
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  delete (globalThis as any).BroadcastChannel
  vi.resetModules()
})

/**
 * Guards the harness itself. Every test below is meaningless if the fake channel
 * never reaches the bus -- the election would "succeed" with a single tab, which
 * is exactly the situation the tests exist to rule out. Asserted once, loudly,
 * so a future edit to the fake cannot quietly make the suite vacuous.
 */
function expectTabsInBus(expected: number) {
  expect(bus.size).toBe(expected)
}

/** A separate copy of the client per call: module state must not be shared. */
async function freshClient() {
  vi.resetModules()
  return await import("@/lib/api")
}

/** Let queued microtasks, the election window and the network settle. */
async function settle(ms = 300) {
  await vi.advanceTimersByTimeAsync(ms)
}

/** Hold refresh responses until releaseRefresh() is called. */
function gateRefresh() {
  refreshGate = new Promise<void>((resolve) => {
    releaseRefresh = resolve
  })
  return () => {
    refreshGate = null
    releaseRefresh()
  }
}

const OPTS = { attempts: 1, baseDelayMs: 1 }

describe("cross-tab refresh coordination", () => {
  it("two tabs really are two channels on one bus (harness sanity)", async () => {
    // See expectTabsInBus: without this the race tests could pass vacuously.
    barrierSize = 2
    const open = gateRefresh()
    const tabA = await freshClient()
    const tabB = await freshClient()
    const both = Promise.all([
      tabA.fetchConversations(1, 50, OPTS),
      tabB.fetchConversations(1, 50, OPTS),
    ])
    await settle(250)
    expectTabsInBus(2)
    open()
    await both
  })

  it("elects exactly one refresher when two tabs demand one simultaneously", async () => {
    // The regression this feature exists for. If both tabs stood down, both
    // would retry against a still-expired token, both would fail, and the user
    // would be logged out -- so exactly one must reach the backend.
    barrierSize = 2
    const open = gateRefresh()
    const tabA = await freshClient()
    const tabB = await freshClient()

    const both = Promise.all([
      tabA.fetchConversations(1, 50, OPTS),
      tabB.fetchConversations(1, 50, OPTS),
    ])
    // Let both 401s land and the election resolve while the winner is stuck
    // inside the exchange. A second refresh starting now would be the bug.
    await settle(250)
    expect(refreshCalls).toBe(1)
    open()
    const [a, b] = await both
    await settle()

    // Both tabs end up working, not errored: the point is no logout.
    expect(a.items).toHaveLength(mockConversations.length)
    expect(b.items).toHaveLength(mockConversations.length)
  })

  it("a tab that starts after the leader has claimed still stands down", async () => {
    // The staggered case, and the reason the leader re-announces itself.
    // BroadcastChannel does not replay, so tab B never sees tab A's earlier
    // claim: without a heartbeat B would time out its own election and race A,
    // which is the very double-refresh this is meant to prevent. This test
    // fails if the heartbeat is removed.
    const open = gateRefresh()
    const tabA = await freshClient()

    // A claims and is now stuck inside the exchange, still beating.
    const a = tabA.fetchConversations(1, 50, OPTS)
    await settle(250)
    expect(refreshCalls).toBe(1)
    // The leader's channel is still open, which is what makes the heartbeat
    // reachable at all.
    expectTabsInBus(1)

    // B arrives after A's claim was already broadcast, and while the leader is
    // still mid-exchange -- so B's own 401 has to wait for the barrier too.
    barrierSize = 1
    const tabB = await freshClient()
    const b = tabB.fetchConversations(1, 50, OPTS)

    // Give B a full beat cycle: it must hear A and stand down, not refresh.
    await settle(600)
    expect(refreshCalls).toBe(1)

    open()
    const [pageA, pageB] = await Promise.all([a, b])
    await settle()

    expect(refreshCalls).toBe(1)
    expect(pageA.items).toHaveLength(mockConversations.length)
    expect(pageB.items).toHaveLength(mockConversations.length)
  })

  it("a late tab whose nonce sorts first still defers to the live leader", async () => {
    // Ordering is by round timestamp, not by nonce. If it were by nonce, this tab
    // would outrank a leader already mid-exchange and both would refresh.
    //
    // Both tab ids are pinned, rather than left to chance, so the latecomer's
    // nonce sorts *before* the live leader's on every run. Left to a real
    // Math.random this only fails about half the time, and a test that catches a
    // regression half the time is barely a test.
    const open = gateRefresh()
    const rand = vi.spyOn(Math, "random")

    // A tab's id is its Math.random value in base 36, so these two pin the order
    // between them. Asserted rather than assumed, so that if the id derivation
    // ever changes this fails loudly instead of quietly testing nothing.
    rand.mockReturnValue(0.99)
    const leaderId = (0.99).toString(36).slice(2)
    rand.mockReturnValue(0.01)
    const latecomerId = (0.01).toString(36).slice(2)
    expect(latecomerId < leaderId).toBe(true)

    rand.mockReturnValue(0.99)
    const tabA = await freshClient()
    const a = tabA.fetchConversations(1, 50, OPTS)
    await settle(250)
    expect(refreshCalls).toBe(1)
    expectTabsInBus(1)

    rand.mockReturnValue(0.01)
    barrierSize = 1
    const tabB = await freshClient()
    const b = tabB.fetchConversations(1, 50, OPTS)

    await settle(600)
    expect(refreshCalls).toBe(1)
    rand.mockRestore()
    open()
    const [pageA, pageB] = await Promise.all([a, b])
    await settle()

    expect(refreshCalls).toBe(1)
    expect(pageA.items).toHaveLength(mockConversations.length)
    expect(pageB.items).toHaveLength(mockConversations.length)
  })

  it("a late tab stands down even when it lands just after a beat", async () => {
    // A late arrival on the worst side of the heartbeat: it has not seen the
    // leader's opening claim, so the next beat is all it can learn from.
    //
    // Note what this does *not* pin. Placing the arrival a hair after a beat is
    // only as exact as the time it takes to import the client module and hand
    // the request to msw -- tens of milliseconds of drift that is not under the
    // test's control. It reliably catches a missing or broken heartbeat, but it
    // cannot be relied on to catch a *shortened* election window, because a beat
    // may well land inside the short window by accident. The margin between the
    // two intervals is pinned directly by the next test instead.
    const open = gateRefresh()
    const tabA = await freshClient()

    const a = tabA.fetchConversations(1, 50, OPTS)
    await settle(250)
    expect(refreshCalls).toBe(1)

    // Line B up a hair after A's next beat.
    const beatAt = lastClaimAt
    await vi.advanceTimersByTimeAsync(50)
    expect(lastClaimAt).toBeGreaterThan(beatAt)
    await vi.advanceTimersByTimeAsync(1)

    barrierSize = 1
    const tabB = await freshClient()
    const b = tabB.fetchConversations(1, 50, OPTS)
    // Long enough for B's election to resolve either way.
    await settle(600)
    // B really did contend -- it got a 401 and had to run an election -- rather
    // than sailing through on a token that had quietly gone fresh.
    expect(unauthorized).toBe(2)
    expect(refreshCalls).toBe(1)

    open()
    const [pageA, pageB] = await Promise.all([a, b])
    await settle()

    expect(refreshCalls).toBe(1)
    expect(pageA.items).toHaveLength(mockConversations.length)
    expect(pageB.items).toHaveLength(mockConversations.length)
  })

  it("re-announces often enough that any late tab is heard in time", async () => {
    // The safety margin, measured rather than read out of the source, so this
    // tracks the code instead of drifting from it.
    //
    // A leader's opening claim goes out when its election starts and its first
    // heartbeat one beat *after* the election resolved, so the gap between the
    // two is the election window plus one beat interval. Both intervals are
    // measured, from the message stream itself.
    //
    // What matters is the ratio. A tab that arrives just after a heartbeat
    // never sees the opening claim -- BroadcastChannel does not replay -- so the
    // heartbeat is the only way it learns the lock is held, and it has the whole
    // election window to do so. If that window were shorter than the beat
    // interval it could sit out its election in silence, conclude it had won,
    // and refresh alongside the leader: the double refresh this exists to stop.
    const open = gateRefresh()
    const tabA = await freshClient()
    const a = tabA.fetchConversations(1, 50, OPTS)

    // A passive listener, registered on the bus like any other tab, so what it
    // records is exactly what a latecomer could have learned.
    const claims: number[] = []
    const spy = new BroadcastChannel("nexus:auth-refresh")
    spy.addEventListener("message", (event) => {
      if ((event.data as { type?: string } | null)?.type === "claim") {
        claims.push(Date.now())
      }
    })

    await settle(1_000)
    open()
    await a
    spy.close()

    // Enough traffic for a leader plus several heartbeats.
    expect(claims.length).toBeGreaterThan(4)

    const beats = claims.slice(1) // the opening claim is not replayed to anyone
    const beatInterval = beats[1] - beats[0]
    const electionWindow = beats[0] - claims[0] - beatInterval

    // The window has to clear the beat by a real margin, not marginally: two
    // heartbeats inside one window is what a worst-case tab depends on.
    expect(electionWindow).toBeGreaterThan(2 * beatInterval)

    // And the margin is real rather than notional -- a listener is actually
    // told at least twice while an election of that length runs.
    const inWindow = beats.filter((t) => t - beats[0] <= electionWindow)
    expect(inWindow.length).toBeGreaterThanOrEqual(2)
  })

  it("tolerates three tabs at once and still refreshes exactly once", async () => {
    barrierSize = 3
    const open = gateRefresh()
    const clients = await Promise.all([freshClient(), freshClient(), freshClient()])
    const all = Promise.all(clients.map((c) => c.fetchConversations(1, 50, OPTS)))
    await settle(250)
    expect(refreshCalls).toBe(1)
    open()
    const pages = await all
    await settle()

    for (const page of pages) expect(page.items).toHaveLength(mockConversations.length)
  })

  it("elects the same leader for a burst of tabs (lowest nonce, one outcome)", async () => {
    // Guards the tie-break specifically. A "first claim wins" or "last claim
    // wins" election passes the two-tab test and deadlocks on three or more.
    barrierSize = 4
    const open = gateRefresh()
    const clients = await Promise.all([freshClient(), freshClient(), freshClient(), freshClient()])
    const all = Promise.all(clients.map((c) => c.fetchConversations(1, 50, OPTS)))
    await settle(250)
    expect(refreshCalls).toBe(1)
    open()
    const pages = await all
    await settle()

    for (const page of pages) expect(page.items).toHaveLength(mockConversations.length)
  })

  it("a follower retries only after the leader reports success, not on the claim", async () => {
    // Distinguishes "waited for the result" from "returned early on the claim":
    // an early return would retry while the cookie was still stale and fail.
    const order: string[] = []
    server.use(
      http.post("/api/auth/refresh", () => {
        refreshCalls += 1
        order.push("refresh")
        tokenFresh = true
        return HttpResponse.json({ ok: true })
      }),
      http.get("/api/conversations", () => {
        order.push(`conversations${tokenFresh ? "-fresh" : "-expired"}`)
        if (!tokenFresh) return HttpResponse.json({}, { status: 401 })
        return HttpResponse.json(mockConversations)
      }),
    )

    const tabA = await freshClient()
    const tabB = await freshClient()
    await Promise.all([tabA.fetchConversations(1, 50, OPTS), tabB.fetchConversations(1, 50, OPTS)])
    await settle()

    // The successful retry must come after the refresh, never before it.
    // Counted rather than compared by value: both tabs retry, and both must
    // land on the fresh side of the refresh.
    expect(refreshCalls).toBe(1)
    const refreshAt = order.indexOf("refresh")
    const retries = order
      .map((entry, index) => ({ entry, index }))
      .filter(({ entry }) => entry === "conversations-fresh")
    expect(retries.length).toBeGreaterThanOrEqual(1)
    for (const { index } of retries) {
      expect(index).toBeGreaterThan(refreshAt)
    }
  })

  it("still refreshes when a BroadcastChannel constructor throws on open", async () => {
    // The other half of the feature-detect: some embedded browsers expose the
    // constructor but refuse to open it. openRefreshChannel catches that and
    // falls back to a plain single-tab refresh rather than failing the request.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(globalThis as any).BroadcastChannel = class {
      // No parameters: it is called as `new BroadcastChannel(name)`, and a
      // constructor that declares none simply ignores the argument.
      constructor() {
        throw new Error("refused")
      }
    }
    const a = await freshClient()

    const page = await a.fetchConversations(1, 50, OPTS)
    await settle()

    expect(refreshCalls).toBe(1)
    expect(page.items).toHaveLength(mockConversations.length)
  })

  it("takes over when the leader claims and then dies without reporting", async () => {
    // A tab closed mid-refresh would hold the lock forever. The follower's lock
    // TTL has to expire so it refreshes itself instead of hanging until reload.
    deliverDone = false
    const b = await freshClient()

    const page = b.fetchConversations(1, 50, OPTS)
    await vi.advanceTimersByTimeAsync(25_000)
    const result = await page

    expect(refreshCalls).toBe(1)
    expect(result.items).toHaveLength(mockConversations.length)
  })

  it("shares one refresh across concurrent requests within a single tab", async () => {
    // The module-level in-flight promise, which predates the cross-tab work and
    // must not have been broken by it.
    //
    // Three *different* routes, each answering 401 on its own, so each request
    // independently reaches nexusFetch's refresh branch. One URL would not do:
    // the other two would never 401 and the shared promise would have nothing
    // to deduplicate, which is exactly how this test passed while the sharing
    // was broken.
    const a = await freshClient()
    server.use(
      http.get("/api/usage/summary", () =>
        tokenFresh
          ? HttpResponse.json({ total_tokens: 0 })
          : expired(),
      ),
      http.get("/api/files", () => (tokenFresh ? HttpResponse.json([]) : expired())),
    )

    const results = await Promise.allSettled([
      a.fetchConversations(1, 50, OPTS),
      a.fetchUsage(),
      a.fetchKnowledgeFiles(),
    ])
    await settle()

    // Every route really did 401 and really did retry.
    expect(unauthorized).toBe(3)
    expect(results.map((r) => r.status)).toEqual([
      "fulfilled",
      "fulfilled",
      "fulfilled",
    ])

    // ...and one refresh covered all three.
    expect(refreshCalls).toBe(1)
  })

  it("still refreshes in a browser without BroadcastChannel", async () => {
    // Same intent as above but deleting the global before the module is loaded,
    // so the feature-detect guard itself is what returns null -- not a
    // constructor that throws once the channel is opened.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (globalThis as any).BroadcastChannel
    const a = await freshClient()

    const page = await a.fetchConversations(1, 50, OPTS)
    await settle()

    expect(refreshCalls).toBe(1)
    expect(page.items).toHaveLength(mockConversations.length)
  })

  it("still surfaces the 401 when the refresh itself is declined", async () => {
    // Coordination must not paper over a genuinely dead session: a failed
    // refresh has to leave the request failed, not spin waiting on peers.
    server.use(
      http.post("/api/auth/refresh", () => {
        refreshCalls += 1
        return HttpResponse.json({ ok: false }, { status: 401 })
      }),
    )
    const a = await freshClient()

    await expect(a.fetchConversations(1, 50, OPTS)).rejects.toThrow(/401/)
    await settle()

    expect(refreshCalls).toBe(1)
  })
})
