import { beforeEach, describe, expect, it, vi } from "vitest"
import { act, fireEvent, render, screen, waitFor } from "@/test/test-utils"
import { TwoFactorSettings } from "@/features/settings/TwoFactorSettings"
import {
  disableTwoFactor,
  enableTwoFactor,
  getTwoFactorStatus,
  setupTwoFactor,
} from "@/lib/api"

vi.mock("@/lib/api", () => ({
  getTwoFactorStatus: vi.fn(),
  setupTwoFactor: vi.fn(),
  enableTwoFactor: vi.fn(),
  disableTwoFactor: vi.fn(),
}))

const mockedStatus = vi.mocked(getTwoFactorStatus)
const mockedSetup = vi.mocked(setupTwoFactor)
const mockedEnable = vi.mocked(enableTwoFactor)
const mockedDisable = vi.mocked(disableTwoFactor)

describe("Two-factor settings", () => {
  beforeEach(() => {
    mockedStatus.mockReset()
    mockedSetup.mockReset()
    mockedEnable.mockReset()
    mockedDisable.mockReset()
  })

  it("enrolls a user by showing the secret and confirming a code", async () => {
    mockedStatus.mockResolvedValue({ enabled: false })
    mockedSetup.mockResolvedValue({
      secret: "MOCK_TOTP_SECRET_FOR_TEST",
      otpauth_uri: "otpauth://totp/Nexus?secret=MOCK_TOTP_SECRET_FOR_TEST",
    })
    mockedEnable.mockResolvedValue({ status: "enabled" })

    render(<TwoFactorSettings />)

    const start = await screen.findByRole("button", {
      name: /enable two-factor authentication/i,
    })
    await act(async () => {
      fireEvent.click(start)
    })

    expect(await screen.findByText(/MOCK_TOTP_SECRET_FOR_TEST/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/authentication code/i), {
      target: { value: "123456" },
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /confirm and enable/i }))
    })

    await waitFor(() => expect(mockedEnable).toHaveBeenCalledWith("123456"))
    expect(
      await screen.findByText(/two-factor authentication is enabled/i),
    ).toBeInTheDocument()
  })

  it("disables an existing enrollment after validating a code", async () => {
    mockedStatus.mockResolvedValue({ enabled: true })
    mockedDisable.mockResolvedValue({ status: "disabled" })

    render(<TwoFactorSettings />)

    expect(
      await screen.findByText(/two-factor authentication is enabled/i),
    ).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/authentication code/i), {
      target: { value: "654321" },
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /disable two-factor authentication/i }))
    })

    await waitFor(() => expect(mockedDisable).toHaveBeenCalledWith("654321"))
    expect(
      await screen.findByText(/two-factor authentication is disabled/i),
    ).toBeInTheDocument()
  })
})
