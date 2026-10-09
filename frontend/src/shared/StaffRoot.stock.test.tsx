// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { fakeServer, NOON, ok, refusal, type Reply, SHOP_BASE, SHOP_ID } from "../testing/fakeServer";
import { go } from "../testing/renderScreen";
import type { ApiAuth } from "./api";
import { type Role } from "./navigation";
import { StaffRoot } from "./StaffRoot";
import { stockItemBody, stockSettingsBody } from "./stock/testing";

/**
 * The stock in the Telegram Mini App, behind its platform switch (the expansion's module I). The client
 * asks the stock's settings once for the active shop: only when the server answers them does anything
 * of the stock exist. Off (404) and "not for you" (403) look the same: nothing is offered, and the
 * stock's addresses are unknown routes.
 */

const OTHER_SHOP = "5a0c6d3e-0000-4000-8000-00000000bbbb";
const NOT_FOUND = refusal(404, "NOT_FOUND", "Topilmadi.");

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
});
afterEach(cleanup);

const bearer = async (): Promise<ApiAuth> => ({ kind: "bearer", token: "session-token" });

function backend(role: Role, stock: () => Reply, options: { permissions?: string[]; shops?: number } = {}) {
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
          headers: options.permissions ? { "X-Qarz-Permissions": "on" } : {},
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
        return stock();
      case `${SHOP_BASE}/stock/items`:
        return ok({ items: [stockItemBody()], next_cursor: null });
      case `${SHOP_BASE}/suppliers`:
        return ok({ suppliers: [], totals: [], next_cursor: null });
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

const on = () => ok(stockSettingsBody());
const probes = (server: ReturnType<typeof fakeServer>) => server.sent.filter((sent) => sent.path === `${SHOP_BASE}/stock/settings`);
const links = () => [...document.querySelectorAll("nav a")].map((link) => link.getAttribute("href"));
const heading = () => screen.getByRole("heading", { level: 1 }).textContent;

describe("the stock switched off", () => {
  it("offers nothing of the stock to an owner, and its addresses are unknown routes", async () => {
    const server = backend("owner", () => NOT_FOUND);
    await miniApp(server);
    await waitFor(() => expect(probes(server)).toHaveLength(1));
    for (const path of ["#/stock", "#/stock-documents", "#/suppliers"]) {
      expect(links()).not.toContain(path);
    }
    expect(screen.queryByText("Ombor")).toBeNull();
    expect(screen.queryByText("Ta'minotchilar")).toBeNull();
    for (const path of ["#/stock", "#/stock/receipt", "#/stock/report", "#/suppliers", "#/stock-documents"]) {
      go(path);
      expect(heading()).toBe("Sahifa topilmadi");
    }
    // Nothing of the stock was asked beyond the one question, and nothing of its module was run.
    expect(server.sent.filter((sent) => sent.path.includes("/stock/") || sent.path.includes("/suppliers"))).toHaveLength(1);
  });

  it("is asked once for the shop, however many screens are opened", async () => {
    const server = backend("owner", () => NOT_FOUND);
    await miniApp(server);
    go("#/customers");
    go("#/catalog");
    go("#/");
    await waitFor(() => expect(screen.queryByText("Yuklanmoqda…")).toBeNull());
    expect(probes(server)).toHaveLength(1);
    expect(probes(server)[0]?.method).toBe("GET");
  });

  it.each([
    ["refused (403): the member may not see the stock", () => refusal(403, "FORBIDDEN", "Ruxsat yo'q.")],
    ["failing (503)", () => refusal(503, "TIMEOUT", "")],
    ["unreachable", (): Reply => "offline"],
    ["answering something that is not the stock's settings", () => ok({ items: [] })],
  ])("is also what a member sees when the question is %s", async (_name, answer) => {
    const server = backend("owner", answer);
    await miniApp(server);
    await waitFor(() => expect(probes(server)).toHaveLength(1));
    expect(links()).not.toContain("#/stock");
    go("#/stock");
    expect(heading()).toBe("Sahifa topilmadi");
    // The workspace itself is untouched by the failure.
    go("#/");
    expect(heading()).toBe("Umumiy ko'rinish");
  });
});

describe("the stock switched on", () => {
  it("gives a seller the stock and no suppliers; the Mini App has no documents section for anyone", async () => {
    const server = backend("seller", on);
    await miniApp(server);
    await waitFor(() => expect(links()).toContain("#/stock"));
    expect(links()).not.toContain("#/suppliers");
    expect(links()).not.toContain("#/stock-documents");
    go("#/suppliers");
    expect(heading()).toBe("Sahifa topilmadi");
  });

  it("gives a manager the stock and the suppliers, and still no documents section in the Mini App", async () => {
    const server = backend("manager", on);
    await miniApp(server, "#/more");
    await waitFor(() => expect(screen.getAllByRole("link", { name: "Ombor" }).length).toBeGreaterThan(0));
    expect(screen.getAllByRole("link", { name: "Ta'minotchilar" })[0]?.getAttribute("href")).toBe("#/suppliers");
    expect(screen.queryByRole("link", { name: "Ombor hujjatlari" })).toBeNull();
    expect([...document.querySelectorAll("a")].map((link) => link.getAttribute("href"))).not.toContain("#/stock-documents");
    go("#/stock-documents");
    expect(heading()).toBe("Sahifa topilmadi");
  });

  it("opens the stock's screen, whose code and text arrive when it is opened", async () => {
    const server = backend("seller", on);
    await miniApp(server);
    await waitFor(() => expect(links()).toContain("#/stock"));
    expect(server.sent.some((sent) => sent.path === `${SHOP_BASE}/stock/items`)).toBe(false);
    go("#/stock");
    expect(heading()).toBe("Ombor");
    const list = await screen.findByRole("list", { name: "Ombordagi tovarlar" });
    expect(within(list).getByRole("link", { name: "Shakar" })).toBeTruthy();
    // On a phone and in the Mini App the list is rows: tables are the web panel's.
    expect(document.querySelector("table")).toBeNull();
  });

  it("keeps the stock from a member whose own permissions do not include it", async () => {
    const server = backend("manager", on, { permissions: ["ledger.view", "suppliers.view"] });
    await miniApp(server, "#/more");
    await waitFor(() => expect(screen.getAllByRole("link", { name: "Ta'minotchilar" }).length).toBeGreaterThan(0));
    expect(screen.queryByRole("link", { name: "Ombor" })).toBeNull();
    go("#/stock");
    expect(heading()).toBe("Sahifa topilmadi");
  });

  it("does not offer a seller the quick receipt even by its address", async () => {
    const server = backend("seller", on);
    await miniApp(server);
    await waitFor(() => expect(links()).toContain("#/stock"));
    go("#/stock/receipt");
    expect(await screen.findByText("Bosh sahifaga qaytish")).toBeTruthy();
    expect(server.writes()).toEqual([]);
  });
});
