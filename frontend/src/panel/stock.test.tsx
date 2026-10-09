// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { catalogs } from "../i18n/catalog";
import { I18nProvider } from "../i18n/I18nProvider";
import type { ApiAuth } from "../shared/api";
import type { Role } from "../shared/navigation";
import { StaffWorkspace } from "../shared/StaffRoot";
import { costedItemBody, DOCUMENT_ID, documentBody, stockSettingsBody } from "../shared/stock/testing";
import { fakeServer, NOON, ok, refusal, SHOP_BASE, SHOP_ID } from "../testing/fakeServer";
import { go } from "../testing/renderScreen";
import { ACTION_GROUPS } from "./ActivityScreen";
import { PANEL_EXTENSION } from "./PanelRoot";
import { DesktopLayout } from "./tables";
import { CSRF, setWidth } from "./testing";

/**
 * The stock in the web panel (the expansion's module I): the same switch as in the Mini App, the
 * documents section that only the panel has, and real tables on a wide screen.
 */

const NOT_FOUND = refusal(404, "NOT_FOUND", "Topilmadi.");

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
});
afterEach(cleanup);

function backend(role: Role, stockOn: boolean) {
  return fakeServer((sent) => {
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
        return ok({ items: [costedItemBody()], next_cursor: null });
      case `${SHOP_BASE}/stock/documents`:
        return ok({ documents: [documentBody({ lines: undefined })], next_cursor: null });
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

describe("the stock in the web panel", () => {
  it("is absent while the switch is off: no section, and the documents' address is an unknown route", async () => {
    const server = backend("owner", false);
    await panel(server, 1280);
    for (const path of ["#/stock", "#/stock-documents", "#/suppliers"]) {
      expect(hrefs()).not.toContain(path);
    }
    go("#/stock-documents");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Sahifa topilmadi");
    expect(server.sent.filter((sent) => sent.path.includes("/stock") || sent.path.includes("/suppliers"))).toEqual([]);
  });

  it("has the stock, the documents and the suppliers for a manager once the switch is on", async () => {
    const server = backend("manager", true);
    await panel(server, 1280);
    await waitFor(() => expect(hrefs()).toContain("#/stock"));
    expect(hrefs()).toContain("#/stock-documents");
    expect(hrefs()).toContain("#/suppliers");
  });

  it("gives a seller the stock only: documents and suppliers are not theirs", async () => {
    const server = backend("seller", true);
    await panel(server, 1280);
    await waitFor(() => expect(hrefs()).toContain("#/stock"));
    expect(hrefs()).not.toContain("#/stock-documents");
    expect(hrefs()).not.toContain("#/suppliers");
    go("#/stock-documents");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Sahifa topilmadi");
  });

  it("draws the stock as a real table on a wide screen, with the cost columns the server sent", async () => {
    const server = backend("manager", true);
    await panel(server, 1280);
    await waitFor(() => expect(hrefs()).toContain("#/stock"));
    go("#/stock");
    const table = await screen.findByRole("table", { name: "Ombordagi tovarlar" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
      "Tovar",
      "Qoldiq",
      "Sotish narxi",
      "O'rtacha tannarx",
      "Tannarx bo'yicha qiymati",
      "Bir birlikdan foyda",
    ]);
    const cells = within(within(table).getAllByRole("row")[1] as HTMLElement).getAllByRole("cell");
    expect(cells.map((cell) => cell.textContent)).toEqual(["7,5 kg ", "15 000 so'm", "12 000 so'm", "90 000 so'm", "3 000 so'm"]);
  });

  it("draws the same list as rows on a narrow screen", async () => {
    const server = backend("manager", true);
    await panel(server, 375);
    await waitFor(() => expect(hrefs()).toContain("#/stock"));
    go("#/stock");
    expect(await screen.findByRole("list", { name: "Ombordagi tovarlar" })).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("lists the documents as a table that links each to its page", async () => {
    const server = backend("manager", true);
    await panel(server, 1280);
    await waitFor(() => expect(hrefs()).toContain("#/stock-documents"));
    go("#/stock-documents");
    const table = await screen.findByRole("table", { name: "Ombor hujjatlari" });
    expect(within(table).getByRole("link", { name: "Kirim № 7" }).getAttribute("href")).toBe(`#/stock-documents/${DOCUMENT_ID}`);
  });
});

describe("the activity log names what the stock records", () => {
  const ACTIONS = [
    "stock.settings_changed",
    "stock.item_changed",
    "stock.document_created",
    "stock.document_changed",
    "stock.document_posted",
    "stock.document_cancelled",
    "supplier.created",
    "supplier.updated",
    "supplier.archived",
    "supplier.unarchived",
    "supplier.payment_recorded",
    "supplier.opening_recorded",
    "supplier.entry_cancelled",
  ];

  it.each(ACTIONS)("%s has a name in Uzbek and in Russian, and they differ from the server's word", (action) => {
    const key = `activity.action.${action}` as keyof typeof catalogs.uz;
    for (const language of ["uz", "ru"] as const) {
      const text = catalogs[language][key];
      expect(typeof text, `${language}: ${action}`).toBe("string");
      expect(text).not.toBe(action);
      expect(String(text).length).toBeGreaterThan(5);
    }
  });

  it("has no name for an action the stock does not record: the server's word is shown for it", () => {
    expect(Object.hasOwn(catalogs.uz, "activity.action.stock.document_printed")).toBe(false);
  });

  it("can be filtered to the stock's and the suppliers' actions, and names their subjects", () => {
    expect(ACTION_GROUPS).toContain("stock");
    expect(ACTION_GROUPS).toContain("supplier");
    for (const key of ["activity.group.stock", "activity.group.supplier", "activity.subject.stock_document", "activity.subject.supplier"]) {
      expect(typeof catalogs.uz[key as keyof typeof catalogs.uz], key).toBe("string");
      expect(typeof catalogs.ru[key as keyof typeof catalogs.ru], key).toBe("string");
    }
  });
});
