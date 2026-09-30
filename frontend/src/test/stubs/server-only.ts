/**
 * Test stub for the `server-only` package.
 *
 * The real module throws unless the bundler is resolving under the
 * `react-server` condition, which is exactly the guard we want in a build and
 * exactly wrong in Vitest: the suite imports route handlers directly in a jsdom
 * environment where that condition is absent, so the real import throws and
 * takes ten tests with it.
 *
 * What the tests are actually checking is handler behaviour — status
 * propagation, method forwarding, bearer handling — not the bundler's ability
 * to keep a server module out of a client graph. That boundary is enforced by
 * `next build`, which fails loudly if a server-only module reaches the client.
 *
 * So we alias `server-only` to this no-op. See `vitest.config.ts`.
 */
export {};
