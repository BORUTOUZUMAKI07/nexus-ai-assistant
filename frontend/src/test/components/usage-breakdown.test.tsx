import { describe, it, expect } from "vitest"
import { render, screen, within } from "@/test/test-utils"
import { UsageView } from "@/features/usage/UsageView"

describe("UsageView breakdown", () => {
  it("breaks down the fields the usage API actually returns", async () => {
    render(<UsageView />)
    expect(await screen.findByText("142,850")).toBeInTheDocument()

    const table = screen.getByRole("table", { name: /token and cost breakdown/i })
    for (const label of [
      "Prompt",
      "Completion",
      "Cached (included in prompt)",
      "Total",
    ]) {
      expect(
        within(table).getByRole("rowheader", { name: label }),
        label,
      ).toBeInTheDocument()
    }

    for (const value of [
      "98,420 tok",
      "44,430 tok",
      "25,110 tok",
      "142,850 tok",
    ]) {
      expect(within(table).getByText(value), value).toBeInTheDocument()
    }
  })

  it("does not invent per-request fields the API does not expose", async () => {
    render(<UsageView />)
    expect(await screen.findByText("142,850")).toBeInTheDocument()
    expect(screen.getByText(/per-request.*not.*available/i)).toBeInTheDocument()
    // Cost is reported once, in aggregate — not attributed to token categories.
    expect(screen.getByText("$0.0417 aggregate")).toBeInTheDocument()
  })
})
