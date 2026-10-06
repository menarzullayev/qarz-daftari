import { describe, expect, it } from "vitest";

import { hrefFor, parseHash } from "./router";

describe("parseHash", () => {
  it.each([
    ["", "/"],
    ["#", "/"],
    ["#/", "/"],
    ["#/customers", "/customers"],
    ["#/customers/", "/customers"],
    ["#/import-export", "/import-export"],
    ["#/customers?tab=overdue", "/customers"],
    ["#/a/b", "/a/b"],
  ])("reads %s as %s", (hash, path) => {
    expect(parseHash(hash)).toBe(path);
  });

  it("treats Telegram's launch fragment as the home route, not as an unknown page", () => {
    const launch = "#tgWebAppData=query_id%3DAAE%26user%3D%257B%2522id%2522%253A1%257D&tgWebAppVersion=8.0";
    expect(parseHash(launch)).toBe("/");
    expect(parseHash("#main")).toBe("/");
  });

  it("does not confuse a longer path with a shorter one", () => {
    expect(parseHash("#/customers-old")).not.toBe("/customers");
    expect(parseHash("#/Customers")).not.toBe("/customers");
  });

  it("round-trips through hrefFor", () => {
    expect(hrefFor("/reports")).toBe("#/reports");
    expect(parseHash(hrefFor("/reports"))).toBe("/reports");
  });
});
