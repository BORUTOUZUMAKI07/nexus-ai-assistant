import { describe, it, expect } from "vitest"
import { render, screen, fireEvent, waitFor } from "@/test/test-utils"
import { AdminView } from "@/components/AdminView"

describe("AdminView", () => {
  it("lists admin users from the admin API", async () => {
    render(<AdminView />)
    expect(await screen.findByText("Lead Administrator")).toBeInTheDocument()
    expect(screen.getByText("admin@nexus.ai")).toBeInTheDocument()
    expect(screen.getByText("Active")).toBeInTheDocument()
  })

  it("toggles a user active status", async () => {
    render(<AdminView />)
    await screen.findByText("Lead Administrator")
    fireEvent.click(screen.getByRole("button", { name: "Disable" }))
    await waitFor(() => expect(screen.getByText("Disabled")).toBeInTheDocument())
  })

  it("switches to the system health tab", async () => {
    render(<AdminView />)
    fireEvent.click(screen.getByText("System health"))
    await waitFor(() => expect(screen.getByText("PostgreSQL")).toBeInTheDocument())
    expect(screen.getAllByText("connected").length).toBeGreaterThanOrEqual(2)
  })

  it("switches to audit logs and shows entries", async () => {
    render(<AdminView />)
    fireEvent.click(screen.getByText("Audit logs"))
    await waitFor(() => expect(screen.getByText("AUTH_LOGIN")).toBeInTheDocument())
    expect(screen.getByText("user")).toBeInTheDocument()
  })

  it("lists lifecycle hook policies on the hooks tab", async () => {
    render(<AdminView />)
    fireEvent.click(screen.getByText("Lifecycle hooks"))
    await waitFor(() => expect(screen.getByText("Block shell exec")).toBeInTheDocument())
    expect(screen.getByText("run_shell")).toBeInTheDocument()
    expect(screen.getAllByText("pre_tool").length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText("block").length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText("global").length).toBeGreaterThanOrEqual(1)
  })

  it("creates a new hook policy from the form", async () => {
    render(<AdminView />)
    fireEvent.click(screen.getByText("Lifecycle hooks"))
    await screen.findByText("Block shell exec")

    fireEvent.change(screen.getByPlaceholderText("Name (e.g. Block shell exec)"), {
      target: { value: "Log web_search" },
    })
    fireEvent.change(screen.getByPlaceholderText("Tool (e.g. run_shell, *)"), {
      target: { value: "web_search" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Create policy" }))

    expect(await screen.findByText("Log web_search")).toBeInTheDocument()
  })

  it("toggles and deletes a hook policy", async () => {
    render(<AdminView />)
    fireEvent.click(screen.getByText("Lifecycle hooks"))
    await screen.findByText("Block shell exec")

    fireEvent.click(screen.getByRole("button", { name: "Disable" }))
    await waitFor(() => expect(screen.getByText("Disabled")).toBeInTheDocument())

    fireEvent.click(screen.getByTitle("Delete policy"))
    await waitFor(() =>
      expect(screen.queryByText("Block shell exec")).not.toBeInTheDocument()
    )
  })
})