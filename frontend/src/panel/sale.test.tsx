// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { I18nProvider } from "../i18n/I18nProvider";
import type { ApiAuth } from "../shared/api";
import type { Role } from "../shared/navigation";
import { StaffWorkspace } from "../shared/StaffRoot";
import { costedSaleBody, OTHER_MEMBER, OTHER_SALE, SALE_ID, saleRowBody, salesListBody, stockItemBody, stockSettingsBody } from "../shared/stock/testing";
import { fakeServer, NOON, ok, refusal, SHOP_BASE, SHOP_ID } from "../testing/fakeServer";
import { go } from "../testing/renderScreen";
import { PANEL_EXTENSION } from "./PanelRoot";
import { DesktopLayout } from "./tables";
import { CSRF, setWidth } from "./testing";

/**
 * A sale for cash in the web panel: the same form as at the counter, and the list of sales as a real
 * table with its filters. Behind the stock's switch like everything of the stock, and in the stock's
 * own section: the panel gains no section for it.
 */

const NOT_FOUND = refusal(404, "NOT_FOUND", "Topilmadi.");

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
});
afterEach(cleanup);

function backend(role: Role, stockOn: boolean) {
  return fakeServer((sent) => {
    if (sent.method === "POST" && sent.path === `${SHOP_BASE}/stock/sales/${SALE_ID}/cancel`) {
      return ok(costedSaleBody({ status: "cancelled", cancelled_at: "2026-10-06T06:40:00+00:00", cancel_reason: "xato" }));
    }
    switch (sent.path) {
      case "/api/v1/me/shops":
        return {
          status: 200,
          body: { items: [{ shop_id: SHOP_ID, name: "Baraka savdo", role, membership_id: "33333333-3333-4333-8333-333333333333" }], active_shop: SHOP_ID },
          headers: stockOn ? { "X-Qarz-Stock": "on" } : {},
        };
      case `${SHOP_BASE}/overview`:
        return ok({ outstanding: 0, debtors: 0, overdue: { amount: 0, customers: 0 }, due_today: 0 });
      case `${SHOP_BASE}/overview/debtors`:
        return ok({ items: [], next_cursor: null });
      case `${SHOP_BASE}/stock/settings`:
        return ok(stockSettingsBody());
      case `${SHOP_BASE}/stock/items`:
        return ok({ items: [stockItemBody()], next_cursor: null });
      case `${SHOP_BASE}/stock/sales`:
        return ok(
          salesListBody([saleRowBody(), saleRowBody({ id: OTHER_SALE, number: 2, created_by: OTHER_MEMBER, seller_role: "seller", mine: false, method: "card" })], {
            // What the server says by default: a manager and the owner take a sale back, a seller does not.
            may_cancel: role !== "seller",
          }),
        );
      case `${SHOP_BASE}/stock/sales/${SALE_ID}`:
        return ok(costedSaleBody());
      default:
        return NOT_FOUND;
    }
  });
}

async function panel(server: ReturnType<typeof fakeServer>, width: number) {
  setWidth(width);
  const connect = async (): Promise<ApiAuth> => ({ kind: "cookie", csrfToken: CSRF });
  render(
    <I18nProvider initialLanguage="uz">
      <DesktopLayout>
        <StaffWorkspace
          entryKey="entry.panel"
          connect={connect}
          fetch={server.fetch}
          now={() => NOON}
          panel={{ extension: PANEL_EXTENSION, onSignedOut: () => undefined, side: () => null, footer: null }}
        />
      </DesktopLayout>
    </I18nProvider>,
  );
  await screen.findAllByRole("navigation");
  await waitFor(() => expect(screen.queryByText("Yuklanmoqda…")).toBeNull());
}

const hrefs = () => [...document.querySelectorAll("nav a")].map((link) => link.getAttribute("href"));
const asked = (server: ReturnType<typeof fakeServer>) =>
  server.sent.filter((sent) => sent.method === "GET" && sent.path === `${SHOP_BASE}/stock/sales`).map((sent) => sent.query);

describe("a cash sale in the web panel", () => {
  it("is nothing while the stock is switched off: its addresses are unknown routes and nothing is asked", async () => {
    const server = backend("owner", false);
    await panel(server, 1280);
    for (const path of ["#/stock/sale", "#/stock/sales", `#/stock/sales/${SALE_ID}`]) {
      go(path);
      expect(screen.getByRole("heading", { level: 1 }).textContent, path).toBe("Sahifa topilmadi");
    }
    expect(server.sent.filter((sent) => sent.path.includes("/stock"))).toEqual([]);
  });

  it("has no section of its own: it is reached from the stock, whose section is the one it had", async () => {
    const server = backend("manager", true);
    await panel(server, 1280);
    await waitFor(() => expect(hrefs()).toContain("#/stock"));
    expect(hrefs().filter((href) => href?.includes("sale"))).toEqual([]);
    go("#/stock");
    expect((await screen.findByRole("link", { name: "Naqd savdo" })).getAttribute("href")).toBe("#/stock/sale");
    expect(screen.getByRole("link", { name: "Naqd savdolar" }).getAttribute("href")).toBe("#/stock/sales");
    go("#/stock/sale");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Naqd savdo");
    expect(await screen.findByLabelText("Tovar: shtrix-kodni skanerlang yoki nom yozing")).toBeTruthy();
  });

  it("lists the sales as a real table on a wide screen, with the filters, asked for by today's date", async () => {
    const server = backend("manager", true);
    await panel(server, 1280);
    await waitFor(() => expect(hrefs()).toContain("#/stock"));
    go("#/stock/sales");
    const table = await screen.findByRole("table", { name: "Naqd savdolar" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Naqd savdo", "Vaqt", "Tovarlar", "Jami", "To'lov usuli", "Kim sotdi"]);
    expect(within(table).getByRole("link", { name: "Naqd savdo № 3" }).getAttribute("href")).toBe(`#/stock/sales/${SALE_ID}`);
    expect(asked(server)).toEqual([{ day_from: "2026-10-06", day_to: "2026-10-06" }]);
    const filters = screen.getByRole("region", { name: "Savdolar filtri" });
    expect(within(filters).getByLabelText("Qaysi kundan")).toBeTruthy();
    expect(within(filters).getByLabelText("Qaysi kungacha")).toBeTruthy();
    expect(within(filters).getByRole("combobox", { name: "Tovar" })).toBeTruthy();
    fireEvent.change(within(filters).getByLabelText("Kim sotdi"), { target: { value: OTHER_MEMBER } });
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ day_from: "2026-10-06", day_to: "2026-10-06", seller_id: OTHER_MEMBER }));
  });

  it("draws the same list as rows on a narrow screen", async () => {
    const server = backend("manager", true);
    await panel(server, 375);
    await waitFor(() => expect(hrefs()).toContain("#/stock"));
    go("#/stock/sales");
    expect(await screen.findByRole("list", { name: "Naqd savdolar" })).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("opens a sale with its lines as a table, the cost the server sent, and the way to take it back", async () => {
    const server = backend("manager", true);
    await panel(server, 1280);
    await waitFor(() => expect(hrefs()).toContain("#/stock"));
    go(`#/stock/sales/${SALE_ID}`);
    const lines = await screen.findByRole("table", { name: "Savdodagi tovarlar" });
    expect(within(lines).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Tovar", "Miqdor", "Narxi", "Summa", "Tannarx", "Foyda"]);
    fireEvent.click(await screen.findByRole("button", { name: "Savdoni bekor qilish" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Nima uchun bekor qilinmoqda?" }), { target: { value: "xato" } });
    fireEvent.click(within(screen.getByRole("group", { name: "Nima uchun bekor qilinmoqda?" })).getByRole("button", { name: "Savdoni bekor qilish" }));
    expect(await screen.findByText("Savdo bekor qilindi.")).toBeTruthy();
    expect(server.writes().map((sent) => [sent.path, sent.body])).toEqual([[`${SHOP_BASE}/stock/sales/${SALE_ID}/cancel`, { reason: "xato" }]]);
  });

  it("does not offer a seller the way to take a sale back: the server said they may not", async () => {
    const server = backend("seller", true);
    await panel(server, 1280);
    await waitFor(() => expect(hrefs()).toContain("#/stock"));
    go(`#/stock/sales/${SALE_ID}`);
    await screen.findByRole("table", { name: "Savdodagi tovarlar" });
    await waitFor(() => expect(asked(server)).toHaveLength(1));
    expect(screen.queryByRole("button", { name: "Savdoni bekor qilish" })).toBeNull();
  });
});
