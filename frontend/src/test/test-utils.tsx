import { render, renderHook, RenderHookOptions, RenderOptions } from "@testing-library/react"
import { QueryClientProvider } from "@tanstack/react-query"
import { ReactElement, ReactNode, useState } from "react"
import { createTestQueryClient } from "@/lib/query-client"

/**
 * Test render entry point shared by every Vitest test.
 *
 * The app shell provides its own query client (see `AppPage`), but the four
 * lazy views and the hooks are rendered directly by tests, so this wrapper
 * installs one too. Each render gets a *fresh* client: without that, a query
 * cached by one test would be served to the next and a handler that only the
 * first test expected to run would never be hit.
 */
function AppWrapper({ children }: { children: ReactNode }) {
  const [queryClient] = useState(() => createTestQueryClient())
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
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
