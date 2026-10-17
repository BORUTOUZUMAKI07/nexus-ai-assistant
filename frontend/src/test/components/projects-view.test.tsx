import { describe, it, expect } from "vitest"
import { render, screen, fireEvent, waitFor } from "@/test/test-utils"
import { ProjectsView } from "@/components/ProjectsView"

/**
 * ProjectsView is the first consumer of the Projects API helpers in
 * `lib/api.ts` — before this view existed, `listProjects`, `createProject`,
 * `updateProject`, `deleteProject` and `moveConversationToProject` were all
 * exported and never called, so the backend CRUD had no reachable surface.
 * These tests exercise that surface against the BFF route mocks.
 */

const projectAlpha = {
  id: "p-alpha",
  name: "Alpha",
  description: null,
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-01T10:00:00Z",
}

const projectBeta = {
  id: "p-beta",
  name: "Beta",
  description: null,
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-01T10:00:00Z",
}

const conversationInAlpha = {
  id: "conv-alpha",
  title: "Alpha chat",
  model: "llama-3.3-70b-versatile",
  is_pinned: false,
  is_archived: false,
  project_id: "p-alpha",
  token_count: 5,
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-01T10:00:00Z",
}

const conversationLoose = {
  id: "conv-loose",
  title: "Loose chat",
  model: "llama-3.3-70b-versatile",
  is_pinned: false,
  is_archived: false,
  project_id: null,
  token_count: 0,
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-01T10:00:00Z",
}

async function mockProjects(projects: unknown[], conversations: unknown[]) {
  const { server } = await import("@/test/mocks/server")
  const { http, HttpResponse } = await import("msw")
  server.use(
    http.get("/api/projects", () => HttpResponse.json(projects)),
    http.get("/api/conversations", () => HttpResponse.json(conversations))
  )
}

describe("ProjectsView", () => {
  it("lists projects and the conversations grouped under them", async () => {
    await mockProjects([projectAlpha, projectBeta], [conversationInAlpha, conversationLoose])
    render(<ProjectsView />)

    expect(await screen.findByText("Alpha")).toBeInTheDocument()
    expect(screen.getByText("Beta")).toBeInTheDocument()

    // Members are only shown once the project is expanded.
    expect(screen.queryByText("Alpha chat")).not.toBeInTheDocument()
    fireEvent.click(screen.getByLabelText("Expand Alpha"))
    expect(await screen.findByText("Alpha chat")).toBeInTheDocument()
  })

  it("creates a project and prepends it to the list", async () => {
    await mockProjects([], [])
    render(<ProjectsView />)

    await screen.findByText(/No projects yet/)

    fireEvent.change(screen.getByLabelText("New project name"), {
      target: { value: "My project" },
    })
    fireEvent.click(screen.getByRole("button", { name: /New project/ }))

    expect(await screen.findByText("My project")).toBeInTheDocument()
  })

  it("renames a project through the API", async () => {
    await mockProjects([projectAlpha], [])
    render(<ProjectsView />)

    await screen.findByText("Alpha")
    fireEvent.click(screen.getByLabelText("Rename Alpha"))

    const input = screen.getByLabelText("Rename Alpha input")
    fireEvent.change(input, { target: { value: "Alpha Renamed" } })
    fireEvent.keyDown(input, { key: "Enter" })

    expect(await screen.findByText("Alpha Renamed")).toBeInTheDocument()
    expect(screen.queryByText("Alpha")).not.toBeInTheDocument()
  })

  it("deletes a project and removes it from the list", async () => {
    await mockProjects([projectAlpha], [])
    render(<ProjectsView />)

    await screen.findByText("Alpha")
    fireEvent.click(screen.getByLabelText("Delete Alpha"))

    await waitFor(() =>
      expect(screen.queryByText("Alpha")).not.toBeInTheDocument()
    )
  })

  it("moves an ungrouped conversation into a project", async () => {
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.get("/api/projects", () => HttpResponse.json([projectAlpha])),
      http.get("/api/conversations", () => HttpResponse.json([conversationLoose])),
      // Echo the patch back with the real id, the way the backend does.
      http.patch("/api/conversations/:id", async ({ request, params }) => {
        const patch = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ ...conversationLoose, ...patch, id: params.id })
      })
    )

    render(<ProjectsView />)
    await screen.findByText("Alpha")
    fireEvent.click(screen.getByLabelText("Expand Alpha"))

    fireEvent.change(screen.getByLabelText("Add conversation to Alpha"), {
      target: { value: "conv-loose" },
    })

    // The member row appears and the "add" picker is gone, because the only
    // ungrouped conversation is now inside the project.
    expect(
      await screen.findByLabelText("Remove Loose chat from Alpha")
    ).toBeInTheDocument()
    expect(
      screen.queryByLabelText("Add conversation to Alpha")
    ).not.toBeInTheDocument()
  })

  it("removes a conversation from a project", async () => {
    const { server } = await import("@/test/mocks/server")
    const { http, HttpResponse } = await import("msw")
    server.use(
      http.get("/api/projects", () => HttpResponse.json([projectAlpha])),
      http.get("/api/conversations", () =>
        HttpResponse.json([conversationInAlpha, conversationLoose])
      ),
      http.patch("/api/conversations/:id", async ({ request, params }) => {
        const patch = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ ...conversationInAlpha, ...patch, id: params.id })
      })
    )

    render(<ProjectsView />)
    await screen.findByText("Alpha")
    fireEvent.click(screen.getByLabelText("Expand Alpha"))

    fireEvent.click(await screen.findByLabelText("Remove Alpha chat from Alpha"))

    await waitFor(() =>
      expect(
        screen.queryByLabelText("Remove Alpha chat from Alpha")
      ).not.toBeInTheDocument()
    )
  })
})
