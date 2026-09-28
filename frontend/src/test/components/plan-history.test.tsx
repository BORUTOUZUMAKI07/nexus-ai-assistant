import { describe, it, expect } from "vitest"
import { render, screen, fireEvent } from "@/test/test-utils"
import PlanHistory from "@/components/PlanHistory"
import type { PlanItem } from "@/lib/api"

const plan = (over: Partial<PlanItem> = {}): PlanItem => ({
  id: "plan-1",
  conversation_id: "conv-1",
  user_id: "user-1",
  title: "Refactor auth",
  summary: "Split the token helpers out.",
  steps: ["read the module", "extract helpers", "run the tests"],
  status: "pending",
  decision_reason: null,
  created_at: "2026-02-01T09:00:00Z",
  updated_at: "2026-02-01T09:00:00Z",
  decided_at: null,
  ...over,
})

describe("PlanHistory", () => {
  it("renders nothing when no plans exist", () => {
    const { container } = render(<PlanHistory plans={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it("summarises the plans and surfaces how many still need a decision", () => {
    render(
      <PlanHistory
        plans={[
          plan(),
          plan({ id: "plan-2", title: "Second", status: "approved" }),
          plan({ id: "plan-3", title: "Third", status: "pending" }),
        ]}
      />
    )
    expect(screen.getByText("Plans")).toBeInTheDocument()
    expect(screen.getByText("3")).toBeInTheDocument()
    expect(screen.getByText("2 awaiting decision")).toBeInTheDocument()
  })

  it("shows no pending counter when every plan is decided", () => {
    render(
      <PlanHistory
        plans={[
          plan({ id: "a", status: "approved" }),
          plan({ id: "b", status: "rejected" }),
        ]}
      />
    )
    expect(screen.queryByText(/awaiting decision/)).not.toBeInTheDocument()
  })

  it("labels each plan with its decision state", () => {
    render(
      <PlanHistory
        plans={[
          plan({ id: "a", title: "Alpha", status: "pending" }),
          plan({ id: "b", title: "Beta", status: "approved" }),
          plan({ id: "c", title: "Gamma", status: "rejected" }),
        ]}
      />
    )
    expect(screen.getByText("Awaiting decision")).toBeInTheDocument()
    expect(screen.getByText("Approved")).toBeInTheDocument()
    expect(screen.getByText("Rejected")).toBeInTheDocument()
    expect(screen.getByText("Alpha")).toBeInTheDocument()
    expect(screen.getByText("Beta")).toBeInTheDocument()
    expect(screen.getByText("Gamma")).toBeInTheDocument()
  })

  it("expands a plan to reveal its steps, then collapses it again", () => {
    render(<PlanHistory plans={[plan()]} />)
    const toggle = screen.getByText("Refactor auth").closest("button")!

    expect(screen.queryByText("extract helpers")).not.toBeInTheDocument()
    fireEvent.click(toggle)
    expect(screen.getByText("extract helpers")).toBeInTheDocument()
    expect(toggle).toHaveAttribute("aria-expanded", "true")

    fireEvent.click(toggle)
    expect(screen.queryByText("extract helpers")).not.toBeInTheDocument()
    expect(toggle).toHaveAttribute("aria-expanded", "false")
  })

  it("shows the decision reason once a plan has been decided", () => {
    render(
      <PlanHistory
        plans={[
          plan({
            status: "rejected",
            decision_reason: "Rejected in UI",
            decided_at: "2026-02-01T10:00:00Z",
          }),
        ]}
      />
    )
    fireEvent.click(screen.getByText("Refactor auth").closest("button")!)
    expect(screen.getByText(/Decision reason: Rejected in UI/)).toBeInTheDocument()
  })

  it("survives a status the server did not document", () => {
    // The TS type says three values, but the backend declares status as a plain
    // string with the allowed set only in the description, so an unexpected
    // value is reachable and must not blank the panel.
    render(
      <PlanHistory
        plans={[plan({ status: "superseded" as PlanItem["status"] })]}
      />
    )
    expect(screen.getByText("Unknown")).toBeInTheDocument()
    // The row is still usable rather than crashing the whole strip.
    fireEvent.click(screen.getByText("Refactor auth").closest("button")!)
    expect(screen.getByText("extract helpers")).toBeInTheDocument()
  })
})
