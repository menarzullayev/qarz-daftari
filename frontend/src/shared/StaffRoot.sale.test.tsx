// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { fakeServer, NOON, ok, refusal, SHOP_BASE, SHOP_ID } from "../testing/fakeServer";
import { go } from "../testing/renderScreen";
import type { ApiAuth } from "./api";
import type { Role } from "./navigation";
import { StaffRoot } from "./StaffRoot";
import { EAN, SALE_ID, saleBody, saleRowBody, salesListBody, stockItemBody, stockSettingsBody } from "./stock/testing";

/**
 * A sale for cash in the Telegram Mini App: where it is reached from, and for whom. It lives in the
 * stock's section, so it exists only while the platform has the stock switched on, and is offered to a
 * member who holds "stock.sell" together with "stock.view". The fake server refuses as the real one
 * does (the sales by "stock.sell"; the items, a barcode and the settings by "stock.view" or
 * "stock.sell"), and nothing a screen asks may be refused.
 */

const ON = { "X-Qarz-Stock": "on" };
const NOT_FOUND = refusal(404, "NOT_FOUND", "Topilmadi.");
const FIND = "Tovar: shtrix-kodni skanerlang yoki nom yozing";

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
});
afterEach(cleanup);

const bearer = async (): Promise<ApiAuth> => ({ kind: "bearer", token: "session-token" });

/** The permissions that open a read or a write of the stock on the server, any one of them. */
function opens(path: string): readonly string[] | null {
  if (path.startsWith(`${SHOP_BASE}/stock/sales`)) {
    return ["stock.sell"];
  }
  if ([`${SHOP_BASE}/stock/items`, `${SHOP_BASE}/stock/lookup`, `${SHOP_BASE}/stock/settings`].includes(path)) {
    return ["stock.view", "stock.sell"];
  }
  return path.startsWith(`${SHOP_BASE}/stock/`) ? ["stock.view"] : null;
}

function backend(role: Role, header: Record<string, string>, permissions?: string[]) {
  const turnedAway: string[] = [];
  const server = fakeServer((sent) => {
    const needs = permissions ? opens(sent.path) : null;
    if (needs && !needs.some((key) => permissions?.includes(key))) {
      turnedAway.push(`${sent.method} ${sent.path}`);
      return refusal(403, "FORBIDDEN_PERMISSION", "Ruxsat yo'q.");
    }
    if (sent.method === "POST" && sent.path === `${SHOP_BASE}/stock/sales`) {
      return ok({ ...saleBody({ total: 15000 }), warnings: [] }, 201);
    }
    switch (sent.path) {
      case "/api/v1/me/shops":
        return {
          status: 200,
          body: { items: [{ shop_id: SHOP_ID, name: "Baraka savdo", role, membership_id: "33333333-3333-4333-8333-333333333333" }], active_shop: SHOP_ID },
          headers: { ...header, ...(permissions ? { "X-Qarz-Permissions": "on" } : {}) },
        };
      case "/api/v1/me/accounts":
        return ok({ items: [] });
      case `${SHOP_BASE}/permissions/mine`:
        return permissions ? ok({ permissions }) : NOT_FOUND;
      case `${SHOP_BASE}/overview`:
        return ok({ outstanding: 0, debtors: 0, overdue: { amount: 0, customers: 0 }, due_today: 0 });
      case `${SHOP_BASE}/overview/debtors`:
        return ok({ items: [], next_cursor: null });
      case `${SHOP_BASE}/stock/settings`:
        return ok(stockSettingsBody());
      case `${SHOP_BASE}/stock/items`:
        return ok({ items: [stockItemBody()], next_cursor: null });
      case `${SHOP_BASE}/stock/lookup`:
        return sent.query["code"] === EAN ? ok(stockItemBody()) : NOT_FOUND;
      case `${SHOP_BASE}/stock/sales`:
        return ok(salesListBody([saleRowBody()]));
      case `${SHOP_BASE}/stock/sales/${SALE_ID}`:
        return ok(saleBody());
      default:
        return NOT_FOUND;
    }
  });
  return Object.assign(server, { turnedAway });
}

async function miniApp(server: ReturnType<typeof backend>, hash = "") {
  window.location.hash = hash;
  render(<StaffRoot entryKey="entry.app" initialLanguage="uz" connect={bearer} fetch={server.fetch} now={() => NOON} customerPage />);
  await screen.findAllByRole("navigation");
  await waitFor(() => expect(screen.queryByText("Yuklanmoqda…")).toBeNull());
}

const asked = (server: ReturnType<typeof backend>) => server.sent.filter((sent) => sent.path.includes("/stock"));
const links = () => [...document.querySelectorAll("nav a")].map((link) => link.getAttribute("href"));
const heading = () => screen.getByRole("heading", { level: 1 }).textContent;
const SALE_ADDRESSES = ["#/stock/sale", "#/stock/sales", `#/stock/sales/${SALE_ID}`];

describe("a cash sale while the stock is switched off", () => {
  it.each(["seller", "manager", "owner"] as const)("is nothing for a %s: no address of it is a route, and nothing is asked", async (role) => {
    const server = backend(role, {});
    await miniApp(server);
    for (const address of SALE_ADDRESSES) {
      go(address);
      expect(heading(), address).toBe("Sahifa topilmadi");
    }
    expect([...document.querySelectorAll("a")].map((link) => link.getAttribute("href")).filter((href) => href?.includes("sale"))).toEqual([]);
    expect(screen.queryByText("Naqd savdo")).toBeNull();
    expect(asked(server)).toEqual([]);
  });

  it("is not switched on by the cash book's switch, nor by holding its permissions", async () => {
    const server = backend("seller", { "X-Qarz-Cash-Book": "on" }, ["ledger.view", "stock.view", "stock.sell", "stock.sell.cancel"]);
    await miniApp(server);
    for (const address of SALE_ADDRESSES) {
      go(address);
      expect(heading(), address).toBe("Sahifa topilmadi");
    }
    expect(asked(server)).toEqual([]);
  });
});

describe("a cash sale while the stock is switched on", () => {
  it("is reached by a seller from the stock, sold in the Mini App, and found in the day's sales", async () => {
    const server = backend("seller", ON);
    await miniApp(server);
    await waitFor(() => expect(links()).toContain("#/stock"));
    // It has no tab of its own: the phone's bar is as it was.
    expect(links().filter((href) => href?.includes("sale"))).toEqual([]);
    go("#/stock");
    const open = await screen.findByRole("link", { name: "Naqd savdo" });
    expect(open.getAttribute("href")).toBe("#/stock/sale");
    go("#/stock/sale");
    expect(heading()).toBe("Naqd savdo");
    const input = await screen.findByLabelText(FIND);
    fireEvent.change(input, { target: { value: EAN } });
    fireEvent.submit(input.closest("form") as HTMLFormElement);
    await screen.findByLabelText("«Shakar»: miqdor (kg)");
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    expect(await screen.findByText("Naqd savdo № 3 yozildi.")).toBeTruthy();
    expect(server.writes().map((sent) => sent.path)).toEqual([`${SHOP_BASE}/stock/sales`]);

    go("#/stock/sales");
    expect(heading()).toBe("Naqd savdolar");
    const list = await screen.findByRole("list", { name: "Naqd savdolar" });
    // Rows on a phone, never a table.
    expect(document.querySelector("table")).toBeNull();
    fireEvent.click(within(list).getByRole("link", { name: "Naqd savdo № 3" }));
    await waitFor(() => expect(window.location.hash).toBe(`#/stock/sales/${SALE_ID}`));
    go(`#/stock/sales/${SALE_ID}`);
    expect(await screen.findByRole("heading", { name: /Naqd savdo № 3/, level: 2 })).toBeTruthy();
    // The server said this member may not take a sale back.
    expect(screen.queryByRole("button", { name: "Savdoni bekor qilish" })).toBeNull();
    // The list stayed loaded behind the sale: it was asked once, and going back asks nothing more.
    go("#/stock/sales");
    await screen.findByRole("list", { name: "Naqd savdolar" });
    expect(asked(server).filter((sent) => sent.method === "GET" && sent.path === `${SHOP_BASE}/stock/sales`)).toHaveLength(1);
  });

  it("is not offered to a member who sees the stock and may not sell, and its addresses ask nothing", async () => {
    const server = backend("seller", ON, ["ledger.view", "stock.view"]);
    await miniApp(server);
    await waitFor(() => expect(links()).toContain("#/stock"));
    go("#/stock");
    await screen.findByRole("list", { name: "Ombordagi tovarlar" });
    expect(within(screen.getByRole("main")).queryByRole("link", { name: "Naqd savdo" })).toBeNull();
    expect(within(screen.getByRole("main")).queryByRole("link", { name: "Naqd savdolar" })).toBeNull();
    const before = asked(server).length;
    for (const address of SALE_ADDRESSES) {
      go("#/");
      go(address);
      expect(await within(screen.getByRole("main")).findByText("Bosh sahifaga qaytish"), address).toBeTruthy();
    }
    expect(asked(server)).toHaveLength(before);
    expect(server.turnedAway).toEqual([]);
  });

  it("is not there for a member who may sell and may not see the stock: the section itself is not theirs", async () => {
    const server = backend("seller", ON, ["ledger.view", "stock.sell", "stock.sell.cancel"]);
    await miniApp(server);
    expect(links()).not.toContain("#/stock");
    for (const address of ["#/stock", ...SALE_ADDRESSES]) {
      go(address);
      expect(heading(), address).toBe("Sahifa topilmadi");
    }
    expect(asked(server)).toEqual([]);
  });

  it("is still not there for a member who writes documents and holds stock.sell without stock.view", async () => {
    // The stock's section opens for them (their documents are reached through it), and the sale does not.
    const server = backend("seller", ON, ["ledger.view", "stock.receive", "stock.sell"]);
    await miniApp(server);
    await waitFor(() => expect(links()).toContain("#/stock"));
    for (const address of SALE_ADDRESSES) {
      go("#/");
      go(address);
      expect(await within(screen.getByRole("main")).findByText("Bosh sahifaga qaytish"), address).toBeTruthy();
    }
    expect(asked(server).filter((sent) => sent.path.includes("/sales") || sent.path.includes("/items"))).toEqual([]);
    expect(server.turnedAway).toEqual([]);
  });

  it("asks only what stock.sell and stock.view open, for a member who holds exactly those two", async () => {
    const server = backend("seller", ON, ["ledger.view", "stock.view", "stock.sell"]);
    await miniApp(server, "#/stock/sale");
    await screen.findByLabelText(FIND);
    go("#/stock/sales");
    await screen.findByRole("list", { name: "Naqd savdolar" });
    expect(server.turnedAway).toEqual([]);
    expect([...new Set(asked(server).map((sent) => sent.path.slice(SHOP_BASE.length)))].sort()).toEqual(["/stock/sales", "/stock/settings"]);
  });
});
