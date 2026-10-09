import { describe, expect, it } from "vitest";

import { catalogs } from "../i18n/catalog";
import { NETWORK_SECTION_IDS, primaryTabCount, STAFF_SECTION_IDS, staffSections, STOCK_SECTION_IDS } from "./navigation";
import { MIN_ROLE } from "./permissions";
import { matchWorkspaceRoute } from "./workspace/routes";

/**
 * Where the network between shops sits in the navigation (the expansion's module J). Its one section
 * exists only when the caller says the server answered that the network is on, and then only for a
 * member who may see it: a manager or an owner by role, or whoever the owner gave `network.view`.
 */

const ids = (...args: Parameters<typeof staffSections>) => staffSections(...args).map((section) => section.id);
const ON = { network: true } as const;

describe("the network's section while the network is off or unknown", () => {
  it.each(["seller", "manager", "owner"] as const)("is not offered to a %s, whatever the client", (role) => {
    for (const features of [undefined, {}, { network: false }, { stock: true }, { cashBook: true, stock: true }]) {
      for (const office of [false, true]) {
        const offered = ids(role, null, features, office);
        expect(offered, JSON.stringify(features)).not.toContain("network");
        // And the rest is exactly what it is with the network switched on, less the one section.
        expect(offered).toEqual(ids(role, null, { ...features, network: true }, office).filter((id) => id !== "network"));
      }
    }
  });

  it("is not offered to a member who holds every network permission, when the network is off", () => {
    const held = new Set(["ledger.view", "network.view", "network.manage", "network.order", "network.fulfil", "network.confirm"]);
    expect(ids("owner", held)).toEqual(ids("owner", new Set(["ledger.view"])));
    expect(ids("owner", held, { stock: true }, true)).not.toContain("network");
  });

  it("is a section of its own: not one of the workspace's, nor one of the stock's", () => {
    expect(NETWORK_SECTION_IDS).toEqual(["network"]);
    expect(STAFF_SECTION_IDS).not.toContain("network");
    expect(STOCK_SECTION_IDS).not.toContain("network");
  });
});

describe("the network's section while the network is on", () => {
  it("is offered to a manager and an owner, in the Mini App and in the panel, and never to a seller by role", () => {
    for (const office of [false, true]) {
      expect(ids("seller", null, ON, office)).not.toContain("network");
      expect(ids("manager", null, ON, office)).toContain("network");
      expect(ids("owner", null, ON, office)).toContain("network");
    }
  });

  it("follows the catalog, and the stock's sections when the stock is on too", () => {
    expect(ids("manager", null, ON).slice(3, 5)).toEqual(["catalog", "network"]);
    expect(ids("manager", null, { ...ON, stock: true }, true).slice(3, 8)).toEqual(["catalog", "stock", "stockDocuments", "suppliers", "network"]);
  });

  it("opens by the permission to see it and by nothing else", () => {
    const sections = (held: string[]) => ids("seller", new Set(held), ON, true);
    expect(sections(["network.view"])).toEqual(["network"]);
    // Acting without seeing opens nothing: every screen of the section reads first.
    expect(sections(["network.manage", "network.order", "network.fulfil", "network.confirm"])).toEqual([]);
    // A manager by role from whom the owner took the network away.
    expect(ids("manager", new Set(["ledger.view", "stock.view"]), ON)).not.toContain("network");
  });

  it("leaves the phone's first tabs as they were: it sits behind More", () => {
    const sections = staffSections("manager", null, ON);
    expect(sections.slice(0, primaryTabCount(sections)).map((section) => section.id)).toEqual(["overview", "customers", "newEntry"]);
    const section = sections.find((candidate) => candidate.id === "network");
    expect(section).toEqual({ id: "network", path: "/network", labelKey: "nav.network" });
    expect(catalogs.uz["nav.network"]).toBe("Hamkorlar");
    expect(catalogs.ru["nav.network"]).toBe("Партнёры");
  });
});

describe("who holds the network's permissions by role", () => {
  it("gives a manager everything but managing the links, which is the owner's", () => {
    expect([MIN_ROLE["network.view"], MIN_ROLE["network.order"], MIN_ROLE["network.fulfil"], MIN_ROLE["network.confirm"]]).toEqual(["manager", "manager", "manager", "manager"]);
    expect(MIN_ROLE["network.manage"]).toBe("owner");
  });
});

describe("the network's addresses", () => {
  const ID = "0f8b1c2d-3e4f-4a5b-8c6d-7e8f9a0b1c2d";
  const view = (path: string) => {
    const match = matchWorkspaceRoute(path);
    return match?.route.screen === "network" ? match.route.view : null;
  };

  it("all belong to the one section, so that one rule decides who opens any of them", () => {
    const paths = [
      "/network",
      "/network/orders/out",
      "/network/orders/in",
      "/network/orders/new",
      "/network/notes",
      "/network/notes/waiting",
      "/network/payments",
      `/network/links/${ID}`,
      `/network/drafts/${ID}`,
      `/network/orders/${ID}`,
      `/network/notes/${ID}`,
    ];
    for (const path of paths) {
      expect(matchWorkspaceRoute(path), path).toMatchObject({ sectionPath: "/network", titleKey: "nav.network", route: { screen: "network" } });
    }
  });

  it("name the screen and what it is of", () => {
    expect(view("/network")).toEqual({ name: "home" });
    expect(view("/network/orders/out")).toEqual({ name: "orders", role: "buyer" });
    expect(view("/network/orders/in")).toEqual({ name: "orders", role: "supplier" });
    expect(view("/network/orders/new")).toEqual({ name: "compose", draftId: null });
    expect(view(`/network/drafts/${ID}`)).toEqual({ name: "compose", draftId: ID });
    expect(view(`/network/orders/${ID}`)).toEqual({ name: "order", orderId: ID });
    expect(view("/network/notes")).toEqual({ name: "notes", waiting: false });
    expect(view("/network/notes/waiting")).toEqual({ name: "notes", waiting: true });
    expect(view(`/network/notes/${ID}`)).toEqual({ name: "note", noteId: ID });
    expect(view(`/network/links/${ID}`)).toEqual({ name: "link", linkId: ID });
    expect(view("/network/payments")).toEqual({ name: "payments" });
  });

  it("are unknown routes for anything that is not an address of the section", () => {
    for (const path of ["/network/", "/network/links", "/network/links/7", `/network/links/${ID}/end`, "/network/orders", "/network/orders/all", `/network/payments/${ID}`, "/networks", "/network/invites"]) {
      expect(matchWorkspaceRoute(path), path).toBeNull();
    }
  });
});
