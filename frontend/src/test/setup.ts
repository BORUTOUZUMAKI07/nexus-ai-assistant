import "@testing-library/jest-dom/vitest"
import { configure } from "@/test/test-utils"
import { server } from "./mocks/server"
import { beforeAll, afterAll, afterEach } from "vitest"
import { cleanup } from "@/test/test-utils"

configure({ asyncUtilTimeout: 5000 })

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }))
afterEach(() => {
  server.resetHandlers()
  cleanup()
})
afterAll(() => server.close())