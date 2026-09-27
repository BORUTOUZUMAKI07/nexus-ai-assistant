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

  it("shows slice monitoring and fairness on the Slices & fairness tab", async () => {
    render(<AdminView />)
    fireEvent.click(screen.getByText("Slices & fairness"))

    await waitFor(() => expect(screen.getByText("Per-model/provider slices")).toBeInTheDocument())
    expect(screen.getByText("alpha / groq")).toBeInTheDocument()
    expect(screen.getByText(/High-volume \/ low-quality flags/)).toBeInTheDocument()
    expect(screen.getByText("Fairness parity")).toBeInTheDocument()
    expect(screen.getByText("deepeval / faithfulness")).toBeInTheDocument()
  })

  it("shows bandit experiment status with win rates", async () => {
    render(<AdminView />)
    fireEvent.click(screen.getByText("Slices & fairness"))

    await waitFor(() =>
      expect(screen.getByText(/Bandit experiments/)).toBeInTheDocument()
    )
    expect(screen.getByText("canary_v1")).toBeInTheDocument()
    expect(screen.getByText(`${(0.7895 * 100).toFixed(1)}%`)).toBeInTheDocument()
  })

  it("lists the optimization evidence trail and runs a loop", async () => {
    render(<AdminView />)
    fireEvent.click(screen.getByText("Optimization"))

    await waitFor(() => expect(screen.getByText("Optimization evidence trail")).toBeInTheDocument())
    expect(screen.getAllByText("chat_system_prompt").length).toBeGreaterThanOrEqual(1)

    fireEvent.change(screen.getByPlaceholderText(/Current system prompt/), {
      target: { value: "You are a precise assistant." },
    })
    fireEvent.click(screen.getByRole("button", { name: "Run optimization" }))
    await waitFor(() => expect(screen.getAllByText("chat_system_prompt").length).toBeGreaterThanOrEqual(2))
  })

  it("shows the compliance audit surface and EU AI Act classification", async () => {
    render(<AdminView />)
    fireEvent.click(screen.getByText("Compliance"))

    await waitFor(() =>
      expect(screen.getByText(/downstream provider of a general-purpose chatbot/)).toBeInTheDocument()
    )
    expect(screen.getByText("System controls")).toBeInTheDocument()
    expect(screen.getByText("Red team & GDPR")).toBeInTheDocument()
    expect(screen.getByText("GDPR exports")).toBeInTheDocument()
    expect(screen.getAllByText(/canary_v1/).length).toBeGreaterThanOrEqual(1)
  })
})