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

function backend(role: Role, header: Record<string, string>, options: { permissions?: string[]; shops?: number } = {}) {
  const items = [{ shop_id: SHOP_ID, name: "Baraka savdo", role, membership_id: "33333333-3333-4333-8333-333333333333" }];
  if (options.shops === 2) {
    items.push({ shop_id: OTHER_SHOP, name: "Ziyo market", role: "owner", membership_id: "33333333-3333-4333-8333-333333333334" });
  }
  return fakeServer((sent) => {
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
