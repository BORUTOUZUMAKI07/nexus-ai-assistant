import { describe, it, expect } from "vitest"
import { render, screen } from "@/test/test-utils"
import { UsageView } from "@/components/UsageView"
import { setAccessToken } from "@/lib/auth"

describe("UsageView", () => {
  it("shows live telemetry once the usage endpoint responds", async () => {
    setAccessToken("tok")
    render(<UsageView />)

    expect(await screen.findByText("142,850")).toBeInTheDocument()
    expect(screen.getByText("$0.0417")).toBeInTheDocument()
    expect(screen.getByText("42")).toBeInTheDocument()
    expect(screen.getByText("872ms")).toBeInTheDocument()
    expect(screen.getByText("98,420")).toBeInTheDocument()
    expect(screen.getByText("44,430")).toBeInTheDocument()
    expect(screen.getByText("25,110")).toBeInTheDocument()
  })
})