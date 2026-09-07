import { render, renderHook, RenderHookOptions, RenderOptions } from "@testing-library/react"
import { ReactElement, ReactNode } from "react"

/**
 * Test render entry point shared by every Vitest test.
 *
 * Nexus currently renders without app-level providers (no router / data
 * fetching client), so `Wrapper` is a passthrough. When a provider is added
 * (e.g. a query client or theme provider), wrap `children` here once instead
 * of editing every test file.
 */
function AppWrapper({ children }: { children: ReactNode }) {
  return <>{children}</>
}

type CustomRenderOptions = Omit<RenderOptions, "wrapper">

function customRender(ui: ReactElement, options?: CustomRenderOptions) {
  return render(ui, { wrapper: AppWrapper, ...options })
}

function customRenderHook<Result, Props>(
  hook: (initialProps: Props) => Result,
  options?: RenderHookOptions<Props>,
) {
  return renderHook(hook, { wrapper: AppWrapper, ...options })
}

export * from "@testing-library/react"
export { customRender as render }
export { customRenderHook as renderHook }