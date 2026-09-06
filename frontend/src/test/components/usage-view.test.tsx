import { describe, it, expect } from "vitest"
import { render, screen } from "@/test/test-utils"
import { UsageView } from "@/components/UsageView"
import { setAccessToken } from "@/lib/auth"

describe("UsageView", () => {
  it("shows live telemetry once the usage endpoint responds", async () => {
    setAccessToken("tok")
    render(<UsageView />)

    expect(await screen.findByText("142,850")).toBeInTheDocument()
    expect(screen.getByText("$0.00")).toBeInTheDocument()
    expect(screen.getByText("Protected")).toBeInTheDocument()
    expect(screen.getByText("Zero API Expense")).toBeInTheDocument()
  })
})