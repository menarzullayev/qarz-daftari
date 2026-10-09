import { describe, expect, it } from "vitest";

import { ROLES, staffSections } from "./navigation";
import { may, mayAny, MIN_ROLE, type PermissionKey } from "./permissions";

const KEYS = Object.keys(MIN_ROLE) as PermissionKey[];
const RANK = { seller: 0, manager: 1, owner: 2 } as const;

describe("what the interface offers a member", () => {
  it.each(KEYS)("offers %s by role when the server named no permissions", (key) => {
    for (const role of ROLES) {
      const expected = RANK[role] >= RANK[MIN_ROLE[key]];
      expect(may({ role }, key)).toBe(expected);
      expect(may({ role, permissions: null }, key)).toBe(expected);
      expect(may({ role, permissions: undefined }, key)).toBe(expected);
    }
  });

  it.each(KEYS)("offers %s by what the server named, whatever the role", (key) => {
    for (const role of ROLES) {
      expect(may({ role, permissions: new Set([key]) }, key)).toBe(true);
      expect(may({ role, permissions: new Set<string>() }, key)).toBe(false);
      expect(may({ role, permissions: new Set(KEYS.filter((other) => other !== key)) }, key)).toBe(false);
    }
  });

  it("does not take an owner's role for a permission the server did not name", () => {
    // The server names everything for an owner; a list without a key means the member lacks it.
    expect(may({ role: "owner", permissions: new Set(["ledger.view"]) }, "shop.delete")).toBe(false);
  });

  it("opens something that needs any one of several permissions", () => {
    const viewer = { role: "seller", permissions: new Set(["payments.record"]) } as const;
    expect(mayAny(viewer, ["credits.record", "payments.record"])).toBe(true);
    expect(mayAny(viewer, ["credits.record", "entries.cancel"])).toBe(false);
    expect(mayAny(viewer, [])).toBe(false);
  });
});

describe("the sections of the workspace by permission", () => {
  const ids = (permissions: readonly string[]) =>
    staffSections("seller", new Set(permissions)).map((section) => section.id);

  it("gives a seller who was granted reports the reports section, and nothing else new", () => {
    const seller = ["ledger.view", "customers.create", "credits.record", "payments.record", "payment_notices.decide"];
    expect(ids(seller)).toEqual(["overview", "customers", "newEntry", "catalog"]);
    expect(ids([...seller, "reports.view"])).toEqual(["overview", "customers", "newEntry", "catalog", "reports"]);
  });

  it("takes the book away from someone who may not see it", () => {
    expect(ids(["payments.record"])).toEqual(["newEntry"]);
    expect(ids([])).toEqual([]);
  });

  it("opens new entry for either kind, and import/export for either half", () => {
    expect(ids(["credits.record"])).toEqual(["newEntry"]);
    expect(ids(["reports.export"])).toEqual(["importExport"]);
    expect(ids(["imports.run"])).toEqual(["importExport"]);
  });

  it("opens the staff section for whoever was given staff.manage, even a seller", () => {
    expect(ids(["staff.manage"])).toEqual(["staff"]);
    expect(staffSections("manager", new Set(["ledger.view"])).map((section) => section.id)).not.toContain("reports");
  });

  it("keeps to the role when the server named nothing", () => {
    expect(staffSections("seller", null)).toEqual(staffSections("seller"));
    expect(staffSections("owner", undefined)).toEqual(staffSections("owner"));
  });
});
