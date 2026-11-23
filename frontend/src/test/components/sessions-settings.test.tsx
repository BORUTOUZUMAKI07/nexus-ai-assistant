import { beforeEach, describe, expect, it, vi } from "vitest"
import { act, fireEvent, render, screen, waitFor } from "@/test/test-utils"
import { SessionsSettings } from "@/features/settings/SessionsSettings"
import { getSessions, revokeSession, type SessionInfo } from "@/lib/api"

vi.mock("@/lib/api", () => ({
  getSessions: vi.fn(),
  revokeSession: vi.fn(),
}))

const mockedGet = vi.mocked(getSessions)
const mockedRevoke = vi.mocked(revokeSession)

const current: SessionInfo = {
  family_id: "fam-current",
  created_at: "2026-11-20T09:00:00+00:00",
  last_seen: "2026-11-23T08:00:00+00:00",
  expires_at: 1_800_000_000,
  user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36",
  is_current: true,
}
const other: SessionInfo = {
  ...current,
  family_id: "fam-phone",
  user_agent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) Mobile/15E148 Safari/604.1",
  is_current: false,
}

describe("SessionsSettings", () => {
  beforeEach(() => {
    mockedGet.mockReset()
    mockedRevoke.mockReset()
    vi.spyOn(window, "confirm").mockReturnValue(true)
  })

  it("lists sessions, marks this device, and offers revoke only for other devices", async () => {
    mockedGet.mockResolvedValue([current, other])

    render(<SessionsSettings />)

    expect(await screen.findByText(/windows/i)).toBeInTheDocument()
    expect(screen.getByText(/iphone/i)).toBeInTheDocument()
    expect(screen.getByText(/this device/i)).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: /revoke/i })).toHaveLength(1)
    expect(mockedGet).toHaveBeenCalledTimes(1)
  })

  it("revokes another device after confirmation and removes it from the list", async () => {
    mockedGet.mockResolvedValue([current, other])
    mockedRevoke.mockResolvedValue(undefined)

    render(<SessionsSettings />)
    await screen.findByText(/iphone/i)
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /revoke/i }))
    })

    await waitFor(() => expect(mockedRevoke).toHaveBeenCalledWith("fam-phone"))
    await waitFor(() => expect(screen.queryByText(/iphone/i)).not.toBeInTheDocument())
    // The current session stays until the user signs out.
    expect(screen.getByText(/windows/i)).toBeInTheDocument()
  })

  it("keeps the row and reports an error when revoke fails", async () => {
    mockedGet.mockResolvedValue([current, other])
    mockedRevoke.mockRejectedValue(new Error("Session not found."))

    render(<SessionsSettings />)
    await screen.findByText(/iphone/i)
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /revoke/i }))
    })

    expect(await screen.findByRole("alert")).toHaveTextContent("Session not found.")
    expect(screen.getByText(/iphone/i)).toBeInTheDocument()
  })

  it("does not call the API when the confirmation is declined", async () => {
    mockedGet.mockResolvedValue([current, other])
    vi.spyOn(window, "confirm").mockReturnValue(false)

    render(<SessionsSettings />)
    await screen.findByText(/iphone/i)
    fireEvent.click(screen.getByRole("button", { name: /revoke/i }))

    expect(mockedRevoke).not.toHaveBeenCalled()
  })

  it("explains an empty inventory instead of showing a blank panel", async () => {
    mockedGet.mockResolvedValue([])

    render(<SessionsSettings />)

    expect(await screen.findByText(/no active sessions/i)).toBeInTheDocument()
  })
})
