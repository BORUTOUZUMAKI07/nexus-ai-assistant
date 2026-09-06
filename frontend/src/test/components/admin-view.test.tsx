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
    fireEvent.click(screen.getByText("System Health"))
    await waitFor(() => expect(screen.getByText("PostgreSQL")).toBeInTheDocument())
    expect(screen.getAllByText("connected").length).toBeGreaterThanOrEqual(2)
  })

  it("switches to audit logs and shows entries", async () => {
    render(<AdminView />)
    fireEvent.click(screen.getByText("Compliance Audit Logs"))
    await waitFor(() => expect(screen.getByText("AUTH_LOGIN")).toBeInTheDocument())
    expect(screen.getByText("user")).toBeInTheDocument()
  })
})