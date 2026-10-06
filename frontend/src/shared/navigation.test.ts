import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { ADMIN_SECTIONS } from "../admin/navigation";
import { catalogs } from "../i18n/catalog";
import {
  MORE_ITEM,
  PRIMARY_TAB_COUNT,
  STAFF_SECTION_IDS,
  isRole,
  primaryTabCount,
  staffSections,
} from "./navigation";

const ids = (role: Parameters<typeof staffSections>[0]) => staffSections(role).map((section) => section.id);

// A seller reads the catalog (technical specification: "Manager, owner; sellers read"), and a manager
// reads the shop settings that only the owner changes (the server's READ_SHOP capability).
const SELLER = ["overview", "customers", "newEntry", "catalog"];
const MANAGER = [...SELLER, "reminders", "reports", "disputes", "importExport", "shopSettings"];
const OWNER = [
  ...SELLER,
  "reminders",
  "reports",
  "disputes",
  "importExport",
  "staff",
  "activityLog",
  "subscription",
  "shopSettings",
];

describe("staff navigation by role (REQ-033)", () => {
  it("gives a seller customers, new entry, overview and the catalog, and nothing else", () => {
    expect(ids("seller")).toEqual(SELLER);
  });

  it("gives a manager the seller sections plus reminders, reports, disputes, import/export, shop settings", () => {
    expect(ids("manager")).toEqual(MANAGER);
  });

  it("gives an owner everything", () => {
    expect(ids("owner")).toEqual(OWNER);
    expect(ids("owner")).toEqual(STAFF_SECTION_IDS);
  });

  it.each(["reports", "reminders", "disputes", "staff", "shopSettings", "subscription", "activityLog", "importExport"])(
    "does not give a seller %s",
    (id) => {
      expect(ids("seller")).not.toContain(id);
    },
  );

  it.each(["staff", "subscription", "activityLog"])("does not give a manager %s", (id) => {
    expect(ids("manager")).not.toContain(id);
  });

  it("has unique ids and paths, and every label exists in the catalogs", () => {
    const sections = [...staffSections("owner"), MORE_ITEM];
    expect(new Set(sections.map((section) => section.id)).size).toBe(sections.length);
    expect(new Set(sections.map((section) => section.path)).size).toBe(sections.length);
    for (const section of sections) {
      expect(section.path.startsWith("/")).toBe(true);
      expect(catalogs.uz[section.labelKey]).toBeTypeOf("string");
      expect(catalogs.ru[section.labelKey]).toBeTypeOf("string");
    }
  });

  it("keeps all of the seller's sections in the phone tab bar, with no More tab", () => {
    const seller = staffSections("seller");
    expect(primaryTabCount(seller)).toBe(seller.length);
    expect(seller).toHaveLength(PRIMARY_TAB_COUNT + 1);
  });

  it("puts a manager's and an owner's sections after the third behind More", () => {
    expect(primaryTabCount(staffSections("manager"))).toBe(PRIMARY_TAB_COUNT);
    expect(primaryTabCount(staffSections("owner"))).toBe(PRIMARY_TAB_COUNT);
    expect(ids("owner").slice(0, PRIMARY_TAB_COUNT)).toEqual(SELLER.slice(0, PRIMARY_TAB_COUNT));
  });

  it("never spends a More tab on a single section", () => {
    const sections = staffSections("owner");
    for (let count = 0; count <= sections.length; count += 1) {
      const some = sections.slice(0, count);
      const hidden = some.length - primaryTabCount(some);
      expect(hidden === 0 || hidden >= 2, `${count} sections`).toBe(true);
      expect(primaryTabCount(some)).toBeLessThanOrEqual(PRIMARY_TAB_COUNT + 1);
    }
  });

  it("recognises only the three roles", () => {
    expect(isRole("owner")).toBe(true);
    expect(isRole("admin")).toBe(false);
    expect(isRole(null)).toBe(false);
  });
});

describe("admin navigation", () => {
  const adminIds = ADMIN_SECTIONS.map((section) => section.id);

  it("has shops, receipts, settings, support access, and audit", () => {
    expect(adminIds).toEqual(["adminShops", "adminReceipts", "adminSettings", "adminSupportAccess", "adminAudit"]);
  });

  it("contains none of the staff sections", () => {
    for (const id of STAFF_SECTION_IDS) {
      expect(adminIds).not.toContain(id);
    }
    const staffLabels = new Set(staffSections("owner").map((section) => section.labelKey));
    for (const section of ADMIN_SECTIONS) {
      expect(staffLabels.has(section.labelKey)).toBe(false);
      expect(section.labelKey.startsWith("admin.nav.")).toBe(true);
    }
  });

  it("is not built from the staff navigation model", () => {
    // Simple source check: no file of the admin entry point may import the staff navigation module.
    const importsStaffNavigation = (source: string) => /from\s+["'][^"']*shared\/navigation["']/.test(source);
    const adminDir = resolve(import.meta.dirname, "../admin");
    const sources = readdirSync(adminDir).filter((name) => /\.tsx?$/.test(name) && !name.includes(".test."));
    expect(sources.length).toBeGreaterThan(0);
    for (const name of sources) {
      expect(importsStaffNavigation(readFileSync(resolve(adminDir, name), "utf8")), name).toBe(false);
    }
    expect(importsStaffNavigation('import { staffSections } from "../shared/navigation";')).toBe(true);
  });
});
