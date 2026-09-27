import { describe, it, expect, vi } from "vitest"
import type { ReactNode } from "react"
import React from "react"
import { fireEvent, render, screen } from "@/test/test-utils"
import RootLoading from "@/app/loading"
import RootError from "@/app/error"
import RootNotFound from "@/app/not-found"
import GlobalError from "@/app/global-error"
import AppLoading from "@/app/app/loading"
import AppError from "@/app/app/error"
import SignInLoading from "@/app/signin/loading"
import SignInError from "@/app/signin/error"

// not-found.tsx is a Server Component using next/link; swap it for a plain
// anchor (created via React.createElement, never JSX <a>) so it renders in
// jsdom without the app router and avoids the no-html-link-for-pages rule.
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) =>
    React.createElement("a", { href }, children),
}))

describe("route status UI", () => {
  it("root loading renders the branded status loader", () => {
    render(<RootLoading />)
    expect(
      screen.getByRole("status", { name: /loading nexus/i }),
    ).toBeInTheDocument()
    expect(screen.getByText("Nexus")).toBeInTheDocument()
  })

  it("sign-in loading renders the compact status loader", () => {
    render(<SignInLoading />)
    expect(
      screen.getByRole("status", { name: /loading sign in/i }),
    ).toBeInTheDocument()
  })

  it("workspace loading renders the app-shell skeleton", () => {
    render(<AppLoading />)
    expect(
      screen.getByRole("status", { name: /loading workspace/i }),
    ).toBeInTheDocument()
  })

  it("root error shows the branded fallback and retries", () => {
    const retry = vi.fn()
    render(<RootError error={new Error("boom")} retry={retry} />)
    expect(
      screen.getByRole("heading", { name: /something went wrong/i }),
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /try again/i }))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it("workspace error shows workspace-scoped copy", () => {
    const retry = vi.fn()
    render(<AppError error={new Error("boom")} retry={retry} />)
    expect(
      screen.getByRole("heading", { name: /workspace error/i }),
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /try again/i }))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it("sign-in error shows auth-scoped copy", () => {
    const retry = vi.fn()
    render(<SignInError error={new Error("boom")} retry={retry} />)
    expect(
      screen.getByRole("heading", { name: /sign-in error/i }),
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /try again/i }))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it("not-found renders the branded 404 with a home link", () => {
    render(<RootNotFound />)
    expect(screen.getByText("404")).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: /back to start/i }),
    ).toHaveAttribute("href", "/")
  })

  it("global-error renders a self-contained fallback document", () => {
    const retry = vi.fn()
    render(<GlobalError error={new Error("fatal")} retry={retry} />)
    expect(
      screen.getByRole("heading", { name: /something went wrong/i }),
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /try again/i }))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it("error components surface the server digest without leaking the message", () => {
    const retry = vi.fn()
    render(<RootError error={new Error("secret detail")} retry={retry} />)
    expect(screen.queryByText(/secret detail/i)).not.toBeInTheDocument()
  })
})