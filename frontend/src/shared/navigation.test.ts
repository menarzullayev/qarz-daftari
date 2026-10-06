import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { ADMIN_SECTIONS } from "../admin/navigation";
import { catalogs } from "../i18n/catalog";
import { MORE_ITEM, PRIMARY_TAB_COUNT, STAFF_SECTION_IDS, isRole, staffSections } from "./navigation";

const ids = (role: Parameters<typeof staffSections>[0]) => staffSections(role).map((section) => section.id);

const SELLER = ["overview", "customers", "newEntry"];
const MANAGER = [...SELLER, "catalog", "reminders", "reports", "disputes", "importExport"];
const OWNER = [...MANAGER, "staff", "activityLog", "subscription", "shopSettings"];

describe("staff navigation by role (REQ-033)", () => {
  it("gives a seller customers, new entry, and overview, and nothing else", () => {
    expect(ids("seller")).toEqual(SELLER);
  });

  it("gives a manager the seller sections plus catalog, reminders, reports, disputes, import/export", () => {
    expect(ids("manager")).toEqual(MANAGER);
  });

  it("gives an owner everything", () => {
    expect(ids("owner")).toEqual(OWNER);
    expect(ids("owner")).toEqual(STAFF_SECTION_IDS);
  });

  it.each(["reports", "staff", "shopSettings", "subscription", "catalog", "activityLog", "importExport"])(
    "does not give a seller %s",
    (id) => {
      expect(ids("seller")).not.toContain(id);
    },
  );

  it.each(["staff", "subscription", "activityLog", "shopSettings"])("does not give a manager %s", (id) => {
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

  it("keeps the seller's sections in the phone tab bar", () => {
    expect(staffSections("seller")).toHaveLength(PRIMARY_TAB_COUNT);
    expect(ids("owner").slice(0, PRIMARY_TAB_COUNT)).toEqual(SELLER);
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
