/**
 * The Organizations + Webhooks panel: the UI that didn't exist before, so these
 * tests pin both halves of the contract — that it renders what the API returns,
 * and that what it *sends* matches the backend's schemas.
 */
import { describe, it, expect } from "vitest"
import { render, screen, fireEvent, waitFor } from "@/test/test-utils"
import { http, HttpResponse } from "msw"
import { server } from "@/test/mocks/server"
import { IntegrationsSettings } from "@/components/IntegrationsSettings"

const ORG = {
  id: "org-1",
  name: "Verify Org",
  slug: "verify-org",
  role: "owner",
}

const HOOK = {
  id: "wh-1",
  url: "https://example.com/nexus-hook",
  events: ["message.completed"],
  is_active: true,
  has_secret: true,
  created_at: "2026-10-28T04:15:59Z",
}

describe("IntegrationsSettings", () => {
  it("lists organizations and webhook endpoints from the API", async () => {
    server.use(
      http.get("/api/orgs", () => HttpResponse.json([ORG])),
      http.get("/api/webhooks", () => HttpResponse.json([HOOK])),
    )
    render(<IntegrationsSettings />)

    expect(await screen.findByText("Verify Org")).toBeInTheDocument()
    expect(screen.getByText("owner")).toBeInTheDocument()
    expect(await screen.findByText("https://example.com/nexus-hook")).toBeInTheDocument()
    // Both event name and signing state come straight off the endpoint row.
    expect(screen.getByText(/message\.completed/)).toBeInTheDocument()
    expect(screen.getByText(/signed/)).toBeInTheDocument()
  })

  it("shows empty states when the account has neither", async () => {
    server.use(
      http.get("/api/orgs", () => HttpResponse.json([])),
      http.get("/api/webhooks", () => HttpResponse.json([])),
    )
    render(<IntegrationsSettings />)

    expect(
      await screen.findByText(/No organizations yet/),
    ).toBeInTheDocument()
    expect(await screen.findByText(/No endpoints registered yet/)).toBeInTheDocument()
  })

  it("registers an endpoint with its events and shows the one-time secret", async () => {
    let sent: { url?: string; events?: string[] } | null = null
    server.use(
      http.get("/api/orgs", () => HttpResponse.json([])),
      http.get("/api/webhooks", () => HttpResponse.json([HOOK])),
      http.post("/api/webhooks", async ({ request }) => {
        sent = (await request.json()) as { url?: string; events?: string[] }
        return HttpResponse.json(
          { ...HOOK, id: "wh-2", url: String((sent as { url?: string }).url), secret: "shhh-123" },
          { status: 201 },
        )
      }),
    )
    render(<IntegrationsSettings />)

    fireEvent.change(await screen.findByLabelText("Webhook URL"), {
      target: { value: "https://hooks.example.com/nexus" },
    })
    fireEvent.click(screen.getByRole("button", { name: /Add endpoint/ }))

    // The payload must match the backend's `WebhookCreate` schema exactly —
    // a missing `events` field is a 422 the user would only see as a failure.
    await waitFor(() => expect(sent).not.toBeNull())
    expect(sent!.url).toBe("https://hooks.example.com/nexus")
    expect(sent!.events).toEqual(["message.completed"])

    // The HMAC secret is returned once, at registration: the UI must show it.
    expect(await screen.findByText(/shhh-123/)).toBeInTheDocument()
    expect(screen.getByText(/shown once/)).toBeInTheDocument()
  })

  it("surfaces a list failure inline instead of rendering a blank card", async () => {
    server.use(
      http.get("/api/orgs", () => HttpResponse.json([])),
      http.get("/api/webhooks", () =>
        HttpResponse.json({ detail: "backend unavailable" }, { status: 503 }),
      ),
    )
    render(<IntegrationsSettings />)

    expect(await screen.findByText("backend unavailable")).toBeInTheDocument()
    // The organizations section still loads: one surface failing must not
    // blank the other.
    expect(await screen.findByText(/No organizations yet/)).toBeInTheDocument()
  })

  it("toggles an endpoint through PATCH with the new state", async () => {
    let patched: { is_active?: boolean } | null = null
    server.use(
      http.get("/api/orgs", () => HttpResponse.json([])),
      http.get("/api/webhooks", () => HttpResponse.json([HOOK])),
      http.patch("/api/webhooks/wh-1", async ({ request }) => {
        patched = (await request.json()) as { is_active?: boolean }
        return HttpResponse.json({ id: "wh-1", is_active: false })
      }),
    )
    render(<IntegrationsSettings />)

    // The row control's label is its state ("Active") and its title is the
    // action; the title is the stable handle here.
    const toggle = await screen.findByTitle("Deactivate")
    fireEvent.click(toggle)

    await waitFor(() => expect(patched).not.toBeNull())
    expect(patched!.is_active).toBe(false)
  })
})
