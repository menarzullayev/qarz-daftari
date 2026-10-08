import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { DESCRIPTION_PATH, generateTypes, isCurrent, TYPES_PATH } from "./apiTypes.ts";

type Description = {
  paths: Record<string, unknown>;
  components: {
    schemas: Record<string, { properties: Record<string, { type?: string }>; required: string[] }>;
  };
};

const description = readFileSync(DESCRIPTION_PATH, "utf8");
const committed = readFileSync(TYPES_PATH, "utf8");

/** The description after the back end changed one thing in it. */
function changed(change: (document: Description) => void): string {
  const document = JSON.parse(description) as Description;
  change(document);
  return JSON.stringify(document);
}

describe("the generated API types", () => {
  it("are what the back end's description generates", async () => {
    // Fails after `backend/openapi.json` changed until `npm run api:types` is run again.
    expect(isCurrent(committed, await generateTypes(description))).toBe(true);
  });

  it("are written with line feeds only, so the check gives the same answer on every platform", async () => {
    expect(committed.includes("\r")).toBe(false);
    expect(description.includes("\r")).toBe(false);
    const generated = await generateTypes(description.replace(/\n/g, "\r\n"));
    expect(generated.includes("\r")).toBe(false);
    expect(generated).toBe(committed);
  });

  it("are no longer current when the back end renames a field of an answer", async () => {
    const renamed = changed((document) => {
      const customer = document.components.schemas["Customer"];
      if (customer) {
        customer.properties["full_name"] = customer.properties["display_name"] ?? {};
        delete customer.properties["display_name"];
        customer.required = customer.required.map((name) => (name === "display_name" ? "full_name" : name));
      }
    });
    const generated = await generateTypes(renamed);
    expect(isCurrent(committed, generated)).toBe(false);
    expect(generated).toContain("full_name: string;");
  });

  it("are no longer current when a field changes its type or a route is added", async () => {
    const retyped = changed((document) => {
      const balance = document.components.schemas["Customer"]?.properties["balance"];
      if (balance) {
        balance.type = "string";
      }
    });
    expect(isCurrent(committed, await generateTypes(retyped))).toBe(false);
    const added = changed((document) => {
      document.paths["/api/v1/added-later"] = { get: { responses: { 200: { description: "ok" } } } };
    });
    expect(isCurrent(committed, await generateTypes(added))).toBe(false);
  });

  it("are not current when the file is missing or was edited by hand", async () => {
    const generated = await generateTypes(description);
    expect(isCurrent(null, generated)).toBe(false);
    expect(isCurrent(committed.replace("display_name", "displayName"), generated)).toBe(false);
  });
});
