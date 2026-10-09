// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { fakeServer, NOON, ok, refusal, SHOP_BASE, SHOP_ID } from "../testing/fakeServer";
import { go } from "../testing/renderScreen";
import type { ApiAuth } from "./api";
import { type Role } from "./navigation";
import { StaffRoot } from "./StaffRoot";
import { DOCUMENT_ID, documentBody, stockItemBody, stockSettingsBody } from "./stock/testing";

const ON = { "X-Qarz-Stock": "on" };

/**
 * The stock in the Telegram Mini App, behind its platform switch (the expansion's module I). The server
 * says that the stock is on with a header of the person's shops, as it does for the cash book; without
 * that header nothing of the stock is offered, its addresses are unknown routes, and nothing of it is
 * asked. With it, each section opens for whoever holds its permission.
 */

const OTHER_SHOP = "5a0c6d3e-0000-4000-8000-00000000bbbb";
const NOT_FOUND = refusal(404, "NOT_FOUND", "Topilmadi.");

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
});
afterEach(cleanup);

const bearer = async (): Promise<ApiAuth> => ({ kind: "bearer", token: "session-token" });

/**
 * The permissions that open a read of the stock on the server, any one of them (the stock's API table
 * of docs/08-technical-spec/OUTPUT.md); null for a path that is not the stock's.
 */
function opens(path: string): readonly string[] | null {
  if (path === `${SHOP_BASE}/stock/report`) {
    return ["stock.costs.view"];
  }
  if (path.startsWith(`${SHOP_BASE}/stock/documents`)) {
    return ["stock.receive", "stock.adjust"];
  }
  if (path.startsWith(`${SHOP_BASE}/stock/`)) {
    return ["stock.view"];
  }
  return path.startsWith(`${SHOP_BASE}/suppliers`) ? ["suppliers.view"] : null;
}

/** What the server refused for want of a permission, by server: a screen must never have asked for it. */
const REFUSED = new WeakMap<object, string[]>();
const refused = (server: ReturnType<typeof fakeServer>) => REFUSED.get(server) ?? [];

function backend(role: Role, header: Record<string, string>, options: { permissions?: string[]; shops?: number } = {}) {
  const items = [{ shop_id: SHOP_ID, name: "Baraka savdo", role, membership_id: "33333333-3333-4333-8333-333333333333" }];
  if (options.shops === 2) {
    items.push({ shop_id: OTHER_SHOP, name: "Ziyo market", role: "owner", membership_id: "33333333-3333-4333-8333-333333333334" });
  }
  const turnedAway: string[] = [];
  const server = fakeServer((sent) => {
    // With the member's own permissions named, the stock answers as the server does: 403 without one.
    const needs = options.permissions ? opens(sent.path) : null;
    if (needs && !needs.some((key) => options.permissions?.includes(key))) {
      turnedAway.push(`${sent.method} ${sent.path}`);
      return refusal(403, "FORBIDDEN_PERMISSION", "Ruxsat yo'q.");
    }
    switch (sent.path) {
      case "/api/v1/me/shops":
        return {
          status: 200,
          body: { items, active_shop: SHOP_ID },
          headers: { ...header, ...(options.permissions ? { "X-Qarz-Permissions": "on" } : {}) },
        };
      case "/api/v1/me/accounts":
        return ok({ items: [] });
      case `${SHOP_BASE}/permissions/mine`:
        return options.permissions ? ok({ permissions: options.permissions }) : NOT_FOUND;
      case `${SHOP_BASE}/overview`:
        return ok({ outstanding: 0, debtors: 0, overdue: { amount: 0, customers: 0 }, due_today: 0 });
      case `${SHOP_BASE}/overview/debtors`:
        return ok({ items: [], next_cursor: null });
      case `${SHOP_BASE}/stock/settings`:
        return ok(stockSettingsBody());
      case `${SHOP_BASE}/stock/items`:
        return ok({ items: [stockItemBody()], next_cursor: null });
      case `${SHOP_BASE}/suppliers`:
        return ok({ suppliers: [], totals: [], next_cursor: null });
      case `${SHOP_BASE}/stock/documents`:
        return ok({ documents: [documentBody({ status: "draft", posted_at: null, lines: undefined })], next_cursor: null });
      case `${SHOP_BASE}/stock/documents/${DOCUMENT_ID}`:
        return ok(documentBody({ status: "draft", posted_at: null }));
      default:
        return NOT_FOUND;
    }
  });
  REFUSED.set(server, turnedAway);
  return server;
}

async function miniApp(server: ReturnType<typeof fakeServer>, hash = "") {
  window.location.hash = hash;
  render(<StaffRoot entryKey="entry.app" initialLanguage="uz" connect={bearer} fetch={server.fetch} now={() => NOON} customerPage />);
  await screen.findAllByRole("navigation");
  await waitFor(() => expect(screen.queryByText("Yuklanmoqda…")).toBeNull());
}

const asked = (server: ReturnType<typeof fakeServer>) =>
  server.sent.filter((sent) => sent.path.includes("/stock") || sent.path.includes("/suppliers"));
const links = () => [...document.querySelectorAll("nav a")].map((link) => link.getAttribute("href"));
const heading = () => screen.getByRole("heading", { level: 1 }).textContent;
/** The phone's "More" screen, where the sections that do not fit the tab bar are listed. */
async function miniAppMore() {
  go("#/more");
  await waitFor(() => expect(screen.queryByText("Yuklanmoqda…")).toBeNull());
}

describe("the stock switched off", () => {
  it("offers nothing of the stock to an owner, and its addresses are unknown routes", async () => {
    const server = backend("owner", {});
    await miniApp(server);
    for (const path of ["#/stock", "#/stock-documents", "#/suppliers"]) {
      expect(links()).not.toContain(path);
    }
    expect(screen.queryByText("Ombor")).toBeNull();
    expect(screen.queryByText("Ta'minotchilar")).toBeNull();
    for (const path of ["#/stock", "#/stock/receipt", "#/stock/report", "#/stock/documents", `#/stock/documents/${DOCUMENT_ID}`, "#/suppliers", "#/stock-documents"]) {
      go(path);
      expect(heading()).toBe("Sahifa topilmadi");
    }
    // Not one request of the stock was made, and none of its module was run.
    expect(asked(server)).toEqual([]);
  });

  it.each([
    ["off", { "X-Qarz-Stock": "off" }],
    ["true", { "X-Qarz-Stock": "true" }],
    ["empty", { "X-Qarz-Stock": "" }],
    ["the cash book's, not the stock's", { "X-Qarz-Cash-Book": "on" }],
  ])("is also what a member sees when the header is %s: only the word 'on' turns it on", async (_name, header) => {
    const server = backend("owner", header);
    await miniApp(server);
    expect(links()).not.toContain("#/stock");
    go("#/stock");
    expect(heading()).toBe("Sahifa topilmadi");
    expect(asked(server)).toEqual([]);
    go("#/");
    expect(heading()).toBe("Umumiy ko'rinish");
  });
});

describe("the stock switched on", () => {
  it("gives a seller the stock and no suppliers; the Mini App has no documents section for anyone", async () => {
    const server = backend("seller", ON);
    await miniApp(server);
    await waitFor(() => expect(links()).toContain("#/stock"));
    expect(links()).not.toContain("#/suppliers");
    expect(links()).not.toContain("#/stock-documents");
    go("#/suppliers");
    expect(heading()).toBe("Sahifa topilmadi");
  });

  it("gives a manager the stock and the suppliers, and still no documents section in the Mini App", async () => {
    const server = backend("manager", ON);
    await miniApp(server, "#/more");
    await waitFor(() => expect(screen.getAllByRole("link", { name: "Ombor" }).length).toBeGreaterThan(0));
    expect(screen.getAllByRole("link", { name: "Ta'minotchilar" })[0]?.getAttribute("href")).toBe("#/suppliers");
    expect(screen.queryByRole("link", { name: "Ombor hujjatlari" })).toBeNull();
    expect([...document.querySelectorAll("a")].map((link) => link.getAttribute("href"))).not.toContain("#/stock-documents");
    go("#/stock-documents");
    expect(heading()).toBe("Sahifa topilmadi");
  });

  it("opens the stock's screen, whose code and text arrive when it is opened", async () => {
    const server = backend("seller", ON);
    await miniApp(server);
    await waitFor(() => expect(links()).toContain("#/stock"));
    // Being on costs no request: the stock is asked nothing until one of its screens is opened.
    expect(asked(server)).toEqual([]);
    go("#/stock");
    expect(heading()).toBe("Ombor");
    const list = await screen.findByRole("list", { name: "Ombordagi tovarlar" });
    expect(within(list).getByRole("link", { name: "Shakar" })).toBeTruthy();
    // On a phone and in the Mini App the list is rows: tables are the web panel's.
    expect(document.querySelector("table")).toBeNull();
  });

  it("keeps the stock from a member whose own permissions do not include it", async () => {
    const server = backend("manager", ON, { permissions: ["ledger.view", "suppliers.view"] });
    await miniApp(server, "#/more");
    await waitFor(() => expect(screen.getAllByRole("link", { name: "Ta'minotchilar" }).length).toBeGreaterThan(0));
    expect(screen.queryByRole("link", { name: "Ombor" })).toBeNull();
    go("#/stock");
    expect(heading()).toBe("Sahifa topilmadi");
  });

  it("leads a manager from the stock to its documents, and from a draft to its form, without a documents section", async () => {
    const server = backend("manager", ON);
    await miniApp(server, "#/stock");
    const open = await screen.findByRole("link", { name: "Hujjatlar va qoralamalar" });
    expect(open.getAttribute("href")).toBe("#/stock/documents");
    go("#/stock/documents");
    expect(heading()).toBe("Ombor hujjatlari");
    const list = await screen.findByRole("list", { name: "Ombor hujjatlari" });
    expect(within(list).getByRole("link", { name: "Davom ettirish" }).getAttribute("href")).toBe(`#/stock/documents/${DOCUMENT_ID}`);
    expect(asked(server).filter((sent) => sent.path.endsWith("/stock/documents")).map((sent) => sent.query)).toEqual([{ status: "draft" }]);
    go(`#/stock/documents/${DOCUMENT_ID}`);
    expect(((await screen.findByLabelText("Miqdor (kg)")) as HTMLInputElement).value).toBe("2");
    // Still the stock's own section: the Mini App gained no tab.
    expect(links()).not.toContain("#/stock-documents");
    expect(server.writes()).toEqual([]);
  });

  it("does not open the documents for a seller even by their address, and asks nothing for them", async () => {
    const server = backend("seller", ON);
    await miniApp(server);
    await waitFor(() => expect(links()).toContain("#/stock"));
    for (const path of ["#/stock/documents", `#/stock/documents/${DOCUMENT_ID}`]) {
      go(path);
      expect(await screen.findByText("Bosh sahifaga qaytish")).toBeTruthy();
    }
    expect(asked(server).filter((sent) => sent.path.includes("/documents"))).toEqual([]);
  });

  it("does not offer a seller the quick receipt even by its address", async () => {
    const server = backend("seller", ON);
    await miniApp(server);
    await waitFor(() => expect(links()).toContain("#/stock"));
    go("#/stock/receipt");
    expect(await screen.findByText("Bosh sahifaga qaytish")).toBeTruthy();
    expect(server.writes()).toEqual([]);
  });
});

/**
 * The stock's section in the Mini App holds the items and, because the Mini App has no documents
 * section, the documents too. The server opens the two by different permissions: the items, a barcode
 * and the stock's settings by "stock.view"; the documents by "stock.receive" or "stock.adjust". So the
 * section opens for any of the three, and each screen asks only for what the member may read. The fake
 * server here refuses as the real one does, and nothing may be refused.
 */
describe("the stock's section by what the member holds", () => {
  const LEDGER = "ledger.view";
  const paths = (server: ReturnType<typeof fakeServer>) => [...new Set(asked(server).map((sent) => sent.path.slice(SHOP_BASE.length)))].sort();
  const inMain = (name: string) => within(screen.getByRole("main")).queryByRole("link", { name });
  const notFound = async (server: ReturnType<typeof fakeServer>, address: string) => {
    const before = asked(server).length;
    go("#/");
    go(address);
    // Not a screen for them (inside the section its heading stays, and the screen says "not found") ...
    expect(await within(screen.getByRole("main")).findByText("Bosh sahifaga qaytish"), address).toBeTruthy();
    // ... and nothing of the stock was asked on the way to saying so.
    expect(asked(server).length, address).toBe(before);
  };
  const DOCUMENT = `/stock/documents/${DOCUMENT_ID}`;
  const ITEM = "#/stock/items/44444444-4444-4444-8444-444444444441";

  it("stock.view alone: the items, and nothing of the documents", async () => {
    const server = backend("seller", ON, { permissions: [LEDGER, "stock.view"] });
    await miniApp(server);
    await waitFor(() => expect(links()).toContain("#/stock"));
    go("#/stock");
    expect(await screen.findByRole("list", { name: "Ombordagi tovarlar" })).toBeTruthy();
    expect(inMain("Hujjatlar va qoralamalar")).toBeNull();
    expect(inMain("Tez kirim")).toBeNull();
    for (const address of ["#/stock/documents", `#${DOCUMENT}`, "#/stock/receipt", "#/stock/report"]) {
      await notFound(server, address);
    }
    expect(paths(server)).toEqual(["/stock/items"]);
    expect(refused(server)).toEqual([]);
  });

  it.each([
    ["stock.receive alone", ["stock.receive"], ["Ochish", "Qoralamani o'chirish"], ["O'tkazish", "Qoralamani o'chirish"]],
    // A receipt is not theirs to post or drop: it is read, since the server lets them, and that is all.
    ["stock.adjust alone", ["stock.adjust"], [], []],
    ["stock.receive and stock.adjust without stock.view", ["stock.receive", "stock.adjust"], ["Ochish", "Qoralamani o'chirish"], ["O'tkazish", "Qoralamani o'chirish"]],
  ])("%s: the documents, read and decided, and no item, setting or form", async (_name, held, onRow, onDocument) => {
    const server = backend("seller", ON, { permissions: [LEDGER, ...held] });
    await miniApp(server);
    // The section is there, and its first screen is the list of documents: the items are not theirs.
    await waitFor(() => expect(links()).toContain("#/stock"));
    go("#/stock");
    expect(heading()).toBe("Ombor");
    const list = await screen.findByRole("list", { name: "Ombor hujjatlari" });
    expect(screen.queryByRole("list", { name: "Ombordagi tovarlar" })).toBeNull();
    const row = within(list).getAllByRole("listitem")[0] as HTMLElement;
    expect([...row.querySelectorAll(".actions a, .actions button")].map((control) => control.textContent)).toEqual(onRow);
    // What they cannot do is said, and not linked: no quick receipt, no way "back" to items.
    expect(screen.getByText(/«Omborni ko'rish» ruxsati ham kerak/)).toBeTruthy();
    expect(inMain("Tez kirim")).toBeNull();
    expect(inMain("Ombor")).toBeNull();
    expect(paths(server)).toEqual(["/stock/documents"]);

    // The same list by its own address.
    go("#/stock/documents");
    expect(heading()).toBe("Ombor hujjatlari");
    expect(await screen.findByRole("list", { name: "Ombor hujjatlari" })).toBeTruthy();

    // A draft opens as it stands, not in the form: the form would search items they may not read.
    go(`#${DOCUMENT}`);
    expect(await screen.findByRole("heading", { name: /Kirim № 7/ })).toBeTruthy();
    expect(screen.queryByLabelText("Miqdor (kg)")).toBeNull();
    const offered = ["O'tkazish", "Tahrirlash", "Qoralamani o'chirish"].filter((name) => screen.queryByRole("button", { name }) !== null);
    expect(offered).toEqual(onDocument);

    for (const address of ["#/stock/receipt", ITEM, "#/stock/report", "#/stock-documents", "#/suppliers"]) {
      await notFound(server, address);
    }
    // Only the documents were ever asked for: no item, no barcode, no setting, no supplier.
    expect(paths(server)).toEqual(["/stock/documents", DOCUMENT]);
    expect(refused(server)).toEqual([]);
    expect(server.writes()).toEqual([]);
  });

  it("none of the three: no section, no screen, no request", async () => {
    const server = backend("seller", ON, { permissions: [LEDGER, "stock.costs.view", "goods.edit"] });
    await miniApp(server);
    expect(links()).not.toContain("#/stock");
    await miniAppMore();
    expect(screen.queryByRole("link", { name: "Ombor" })).toBeNull();
    for (const address of ["#/stock", "#/stock/documents", `#${DOCUMENT}`, "#/stock/receipt", ITEM, "#/stock/report"]) {
      await notFound(server, address);
    }
    expect(asked(server)).toEqual([]);
    expect(refused(server)).toEqual([]);
  });

  it("all three: the items, the documents from them, a draft in its form and the quick receipt", async () => {
    const server = backend("seller", ON, { permissions: [LEDGER, "stock.view", "stock.receive", "stock.adjust"] });
    await miniApp(server);
    await waitFor(() => expect(links()).toContain("#/stock"));
    go("#/stock");
    expect(await screen.findByRole("list", { name: "Ombordagi tovarlar" })).toBeTruthy();
    expect(inMain("Hujjatlar va qoralamalar")?.getAttribute("href")).toBe("#/stock/documents");
    expect(inMain("Tez kirim")?.getAttribute("href")).toBe("#/stock/receipt");
    go("#/stock/documents");
    const list = await screen.findByRole("list", { name: "Ombor hujjatlari" });
    expect(within(list).getByRole("link", { name: "Davom ettirish" })).toBeTruthy();
    expect(screen.queryByText(/«Omborni ko'rish» ruxsati ham kerak/)).toBeNull();
    expect(inMain("Ombor")?.getAttribute("href")).toBe("#/stock");
    go(`#${DOCUMENT}`);
    expect(((await screen.findByLabelText("Miqdor (kg)")) as HTMLInputElement).value).toBe("2");
    go("#/stock/receipt");
    expect(await screen.findByRole("heading", { name: "Kirim" })).toBeTruthy();
    expect(paths(server)).toEqual(["/stock/documents", DOCUMENT, "/stock/items", "/stock/settings"]);
    expect(refused(server)).toEqual([]);
  });

  it("would be noticed: the fake server refuses what the real one refuses", async () => {
    const server = backend("seller", ON, { permissions: [LEDGER, "stock.receive"] });
    const answer = async (path: string) => (await server.fetch(`https://qarz.test${SHOP_BASE}${path}`, { method: "GET", headers: {} })).status;
    expect(await answer("/stock/settings")).toBe(403);
    expect(await answer("/stock/items")).toBe(403);
    expect(await answer("/stock/lookup?code=1")).toBe(403);
    expect(await answer("/suppliers")).toBe(403);
    expect(await answer("/stock/documents")).toBe(200);
    expect(refused(server)).toEqual([
      `GET ${SHOP_BASE}/stock/settings`,
      `GET ${SHOP_BASE}/stock/items`,
      `GET ${SHOP_BASE}/stock/lookup`,
      `GET ${SHOP_BASE}/suppliers`,
    ]);
  });
});
