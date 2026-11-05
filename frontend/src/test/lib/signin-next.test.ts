import { describe, expect, it } from "vitest";
import { resolveNextPath } from "@/app/signin/page";

/**
 * `?next=` is how a protected page (and the organization invite link) hands the
 * user back to what they were doing after sign-in. It is also attacker-
 * controllable, so the only acceptable values are same-origin paths: honouring
 * an absolute URL here would let anyone bounce freshly authenticated users onto
 * their own site from Nexus's own login page.
 */
describe("resolveNextPath", () => {
  it("defaults to the app when the URL carries no next", () => {
    expect(resolveNextPath("")).toBe("/app");
    expect(resolveNextPath("?error=oauth_no_token")).toBe("/app");
  });

  it("honours a same-origin path", () => {
    expect(resolveNextPath("?next=%2Fsettings")).toBe("/settings");
  });

  it("keeps an invite link's query string intact", () => {
    expect(resolveNextPath("?next=%2Finvite%3Ftoken%3Dabc")).toBe("/invite?token=abc");
  });

  it.each([
    ["scheme-relative", "%2F%2Fevil.com"],
    ["absolute https", "https%3A%2F%2Fevil.com"],
    ["absolute http", "http%3A%2F%2Fevil.com"],
    ["backslash host", "%5Cevil.com"],
    ["no leading slash", "evil.com"],
  ])("rejects an off-site redirect (%s)", (_label, encoded) => {
    expect(resolveNextPath(`?next=${encoded}`)).toBe("/app");
  });
});
