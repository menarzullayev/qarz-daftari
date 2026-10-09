import { describe, expect, it } from "vitest";

import { catalogs } from "../i18n/catalog";
import { primaryTabCount, STAFF_SECTION_IDS, staffSections, STOCK_SECTION_IDS } from "./navigation";
import { matchWorkspaceRoute, STOCK_DOCUMENT_KINDS } from "./workspace/routes";

/**
 * Where the stock sits in the navigation (the expansion's module I). Its sections exist only when the
 * caller says the server answered that the stock is on; then each opens for whoever holds its
 * permission, and the documents section is the web panel's alone.
 */

const ids = (...args: Parameters<typeof staffSections>) => staffSections(...args).map((section) => section.id);
const ON = { stock: true } as const;

describe("the stock's sections while the stock is off or unknown", () => {
  it.each(["seller", "manager", "owner"] as const)("are not offered to a %s, whatever the client", (role) => {
    for (const features of [undefined, {}, { stock: false }, { cashBook: false }]) {
      for (const office of [false, true]) {
        const offered = ids(role, null, features, office);
        for (const id of STOCK_SECTION_IDS) {
          expect(offered, JSON.stringify(features)).not.toContain(id);
        }
        // And the rest is exactly what it was before the stock existed.
        expect(offered).toEqual(ids(role));
      }
    }
  });

  it("are not among the base sections, which stay as they were", () => {
    for (const id of STOCK_SECTION_IDS) {
      expect(STAFF_SECTION_IDS).not.toContain(id);
    }
  });

  it("are not switched on by the cash book's switch, nor the cash book by the stock's", () => {
    expect(ids("owner", null, { cashBook: true }).filter((id) => STOCK_SECTION_IDS.includes(id))).toEqual([]);
    expect(ids("owner", null, ON)).not.toContain("cash");
    expect(ids("owner", null, { cashBook: true, stock: true }, true)).toEqual(expect.arrayContaining(["cash", "stock", "stockDocuments", "suppliers"]));
  });

  it("are not offered to a member who holds every stock permission, when the stock is off", () => {
    const held = new Set(["ledger.view", "stock.view", "stock.receive", "stock.adjust", "suppliers.view"]);
    expect(ids("seller", held)).toEqual(["overview", "customers", "catalog"]);
  });
});

describe("the stock's sections while the stock is on", () => {
  it("give a seller the stock, after the catalog, and nothing else of it", () => {
    expect(ids("seller", null, ON)).toEqual(["overview", "customers", "newEntry", "catalog", "stock"]);
    expect(ids("seller", null, ON, true)).toEqual(["overview", "customers", "newEntry", "catalog", "stock"]);
  });

  it("give a manager the stock and the suppliers in the Mini App, and the documents too in the panel", () => {
    expect(ids("manager", null, ON).slice(3, 6)).toEqual(["catalog", "stock", "suppliers"]);
    expect(ids("manager", null, ON)).not.toContain("stockDocuments");
    expect(ids("manager", null, ON, true).slice(3, 7)).toEqual(["catalog", "stock", "stockDocuments", "suppliers"]);
    expect(ids("owner", null, ON, true)).toEqual(expect.arrayContaining(["stock", "stockDocuments", "suppliers"]));
  });

  it("follow the permissions the server named, one section for each", () => {
    const sections = (held: string[]) => ids("seller", new Set(held), ON, true).filter((id) => STOCK_SECTION_IDS.includes(id));
    expect(sections(["stock.view"])).toEqual(["stock"]);
    expect(sections(["stock.receive"])).toEqual(["stockDocuments"]);
    expect(sections(["stock.adjust"])).toEqual(["stockDocuments"]);
    expect(sections(["suppliers.view"])).toEqual(["suppliers"]);
    // Paying or keeping the suppliers without being able to read them opens nothing.
    expect(sections(["suppliers.pay", "suppliers.manage", "stock.costs.view"])).toEqual([]);
    // A manager by role from whom the owner took the stock away.
    expect(ids("manager", new Set(["ledger.view"]), ON, true)).toEqual(["overview", "customers", "catalog"]);
  });

  it("keep the phone's tab bar to three tabs and More once a seller has five sections", () => {
    const seller = staffSections("seller", null, ON);
    expect(primaryTabCount(seller)).toBe(3);
    expect(seller.slice(3).map((section) => section.id)).toEqual(["catalog", "stock"]);
  });

  it("have unique ids and paths, and a label in both languages that is in the first load", () => {
    const sections = staffSections("owner", null, ON, true);
    expect(new Set(sections.map((section) => section.id)).size).toBe(sections.length);
    expect(new Set(sections.map((section) => section.path)).size).toBe(sections.length);
    for (const section of sections.filter((candidate) => STOCK_SECTION_IDS.includes(candidate.id))) {
      expect(catalogs.uz[section.labelKey]).toBeTypeOf("string");
      expect(catalogs.ru[section.labelKey]).toBeTypeOf("string");
    }
  });
});

describe("the stock's addresses", () => {
  const ID = "44444444-4444-4444-8444-444444444441";

  it("belong to the section that decides who may open them", () => {
    const section = (path: string) => matchWorkspaceRoute(path)?.sectionPath;
    expect(section("/stock")).toBe("/stock");
    expect(section("/stock/receipt")).toBe("/stock");
    expect(section("/stock/report")).toBe("/stock");
    expect(section(`/stock/items/${ID}`)).toBe("/stock");
    // The documents as a phone lists them are the stock's: the Mini App has no documents section.
    expect(section("/stock/documents")).toBe("/stock");
    expect(section(`/stock/documents/${ID}`)).toBe("/stock");
    expect(section("/stock-documents")).toBe("/stock-documents");
    expect(section(`/stock-documents/${ID}`)).toBe("/stock-documents");
    expect(section("/suppliers")).toBe("/suppliers");
    expect(section(`/suppliers/${ID}`)).toBe("/suppliers");
  });

  it("name their screen", () => {
    expect(matchWorkspaceRoute(`/stock/items/${ID}`)?.route).toEqual({ screen: "stock", view: { name: "item", itemId: ID } });
    expect(matchWorkspaceRoute(`/suppliers/${ID}`)?.route).toEqual({ screen: "stock", view: { name: "supplier", supplierId: ID } });
    expect(matchWorkspaceRoute("/stock/documents")?.route).toEqual({ screen: "stock", view: { name: "counterDocuments" } });
    expect(matchWorkspaceRoute(`/stock/documents/${ID}`)?.route).toEqual({ screen: "stock", view: { name: "counterDocument", documentId: ID } });
    expect(matchWorkspaceRoute(`/stock-documents/${ID}`)?.route).toEqual({ screen: "stock", view: { name: "document", documentId: ID } });
    for (const kind of STOCK_DOCUMENT_KINDS) {
      expect(matchWorkspaceRoute(`/stock-documents/new/${kind}`)?.route).toEqual({ screen: "stock", view: { name: "newDocument", kind } });
    }
  });

  it("are not matched for anything that is not an identifier or a kind of document", () => {
    for (const path of [
      "/stock/items/7",
      "/stock/items/",
      `/stock/items/${ID}/edit`,
      "/stock-documents/new/gift",
      "/stock/documents/7",
      "/stock/documents/new",
      `/stock/documents/${ID}/cancel`,
      "/stock-documents/new",
      "/stock-documents/new/receipt/x",
      "/suppliers/abc",
      "/stock/settings",
      "/stocks",
    ]) {
      expect(matchWorkspaceRoute(path), path).toBeNull();
    }
  });

  it("have a title that is loaded with the first screen", () => {
    for (const path of ["/stock", "/stock/receipt", "/stock-documents", "/suppliers"]) {
      const key = matchWorkspaceRoute(path)?.titleKey;
      expect(key && catalogs.uz[key]).toBeTypeOf("string");
    }
  });
});
