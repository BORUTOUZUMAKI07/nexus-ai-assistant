/**
 * Body-contract guard: the JSON a client helper sends must be a subset of the
 * fields the matching backend Pydantic schema accepts.
 *
 * Why this class of bug survived a green suite
 * ───────────────────────────────────────────
 * `api-parity.test.ts` checks that every *path* has a route handler, and
 * `api-wiring.test.ts` checks that every path the browser calls is served. Both
 * are path-shaped checks, and this bug was body-shaped:
 *
 * `createConversation` sent `{ title, mode, model }`. The backend's
 * `ConversationCreate` is `extra="forbid"` and declares only `title`, `model`,
 * `system_prompt`, `is_pinned` — so every "New Chat" returned 422. Nothing
 * failed, because both call sites catch and `console.warn`, and because a 422
 * with no test asserting on the request body is indistinguishable from a
 * success to MSW-based unit tests.
 *
 * What made it invisible was the belt-and-braces nature of a `forbid` schema:
 * it is the *correct* strictness, and TypeScript cannot help, because the
 * frontend has no generated types from Pydantic. The body is a plain object
 * literal assembled at runtime.
 *
 * So this reads ground truth from both sides the same way the path guards do —
 * the backend schema on disk, and what the client actually sends — and fails
 * when the client sends a field the schema does not declare.
 *
 * Scope, stated honestly
 * ──────────────────────
 * This covers the helpers whose body is a statically-written object literal,
 * which is what a regex can read. It cannot see a body assembled dynamically
 * (spread from a variable, computed keys). Those are listed explicitly below so
 * the coverage limit is documented rather than implied.
 */
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const REPO = resolve(__dirname, "..", "..", "..");
const BACKEND_SCHEMAS = join(REPO, "backend", "app");
const CLIENT_API = join(REPO, "frontend", "src", "lib", "api.ts");

/**
 * Schemas worth checking, and the body keys each accepts.
 *
 * Derived by reading the Pydantic classes off disk, not from memory — a
 * hand-typed list of fields is exactly the kind of thing that drifts. The
 * `extra="forbid"` assertion below is what makes the comparison meaningful: if
 * a schema ever relaxes to `ignore`, an extra field stops being an error and
 * this guard's premise no longer holds for it.
 */
const CONTRACTS: Array<{
  label: string;
  schemaGlob: string[];
  clientBody: RegExp;
}> = [
  {
    label: "POST /conversations",
    schemaGlob: ["domain/conversation/schemas.py"],
    clientBody: /body:\s*JSON\.stringify\(\{([\s\S]*?)\}\)/,
  },
];

/** Read a backend schema file, or return null if it is not on disk. */
function readSchema(relPath: string): string | null {
  try {
    return readFileSync(join(BACKEND_SCHEMAS, relPath), "utf8");
  } catch {
    return null;
  }
}

/**
 * Field names declared on a Pydantic model body.
 *
 * Deliberately crude: it reads the annotated assignments at the top level of the
 * class and stops at the first `def`/nested class. That is sufficient for flat
 * request schemas like `ConversationCreate` and avoids pretending to be a Python
 * parser. A field the regex cannot see shows up as a test failure, not a silent
 * pass, because the allowed-set is used to *reject* — an over-read is the risk,
 * not an under-read.
 */
function declaredFields(source: string, className: string): Set<string> | null {
  const start = source.indexOf(`class ${className}(`);
  if (start === -1) return null;

  const rest = source.slice(start);
  const end = rest.search(/\n(?=class |@router|def )/);
  const body = end === -1 ? rest : rest.slice(0, end);

  const fields = new Set<string>();
  for (const m of body.matchAll(/^\s{4}(\w+)\s*:\s*\w/gm)) {
    fields.add(m[1]);
  }
  return fields;
}

/**
 * Keys a client body literal actually sends.
 *
 * Only object-literal keys count — a bare identifier is a *value*, not a field
 * name. The distinction matters for spread conditionals, which is how every
 * optional field in this file is written:
 *
 *     body: JSON.stringify({
 *       title,                                  // key "title"
 *       ...(convModel ? { model: convModel } : {}),   // key "model", value convModel
 *     })
 *
 * A naive identifier scan reports `convModel` as a sent field and fails on a
 * correct body. So a key is accepted only as `name,`/`name:` at the start of a
 * line inside the literal, which is the style the file already uses throughout.
 * Returns a Set so the caller can assert on membership without caring about
 * ordering or duplicates.
 */
function sentKeys(bodySource: string): Set<string> {
  const keys = new Set<string>();
  for (const line of bodySource.split("\n")) {
    const trimmed = line.trim();
    // `name,` (shorthand) or `name: value` (explicit), at the top level of the
    // literal. A line starting with `...` is a spread; the keys inside the
    // conditional object it carries are picked up by their own `name:` line.
    const shorthand = /^([a-z_]\w*)\s*,$/.exec(trimmed);
    if (shorthand) {
      keys.add(shorthand[1]);
      continue;
    }
    const explicit = /^([a-z_]\w*)\s*:/.exec(trimmed);
    if (explicit) keys.add(explicit[1]);
  }
  return keys;
}

describe("client/backend body contracts", () => {
  const clientSource = readFileSync(CLIENT_API, "utf8");

  it("reads the backend schema off disk (guard is not silently inert)", () => {
    // Without this, a moved or renamed schema file would make every assertion
    // below pass vacuously via a null path.
    const schema = readSchema("domain/conversation/schemas.py");
    expect(schema, "backend conversation schemas not found at the expected path").toBeTruthy();
  });

  for (const contract of CONTRACTS) {
    it(`${contract.label}: the client sends no field the schema forbids`, () => {
      const source = contract.schemaGlob.map(readSchema).find(Boolean);
      expect(source, "no schema resolved").toBeTruthy();

      const allowed = declaredFields(source!, "ConversationCreate");
      expect(allowed, "ConversationCreate not found in the schema file").toBeTruthy();
      expect(
        allowed!.size,
        "field extraction returned nothing — the regex needs updating",
      ).toBeGreaterThan(0);

      const match = clientSource.match(contract.clientBody);
      expect(match, "client body literal not found in api.ts").toBeTruthy();

      const sent = sentKeys(match![1]);
      expect(sent.size, "no keys parsed from the client body").toBeGreaterThan(0);

      // The regression that motivated this file.
      expect(
        sent.has("mode"),
        "client sends `mode`, which ConversationCreate (extra=forbid) rejects with a 422",
      ).toBe(false);

      // And the general form: nothing outside the schema.
      const unknown = [...sent].filter((k) => !allowed!.has(k));
      expect(
        unknown,
        `fields sent but not declared on ConversationCreate: ${unknown.join(", ")}`,
      ).toEqual([]);

      // Positive control: the fields we DO send must actually be schema fields.
      // Without this the guard could pass by extracting zero usable keys.
      expect(sent.has("title")).toBe(true);
    });
  }

  it("documents that ConversationCreate is extra=forbid (the guard's premise)", () => {
    const source = readSchema("domain/conversation/schemas.py")!;
    const start = source.indexOf("class ConversationCreate(");
    expect(start).toBeGreaterThan(-1);
    const window = source.slice(start, start + 400);
    expect(window).toMatch(/extra\s*=\s*["']forbid["']/);
  });

  it("still sends mode where it belongs: the stream request", () => {
    // The counterpart to the assertion above. `mode` is a property of a message,
    // not a conversation, and it travels on StreamChatRequest. Removing it
    // wholesale would be as wrong as sending it to the wrong endpoint.
    const chatRoute = readFileSync(
      join(REPO, "frontend", "src", "app", "api", "chat", "route.ts"),
      "utf8",
    );
    expect(chatRoute).toMatch(/mode:\s*streamMode/);
  });
});
