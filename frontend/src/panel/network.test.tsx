// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { I18nProvider } from "../i18n/I18nProvider";
import type { ApiAuth } from "../shared/api";
import type { Role } from "../shared/navigation";
import { linkBody, NET_LINK_ID, NETWORK, NOTE_ID, noteBody, overviewBody, waitingBody } from "../shared/network/testing";
import { StaffWorkspace } from "../shared/StaffRoot";
import { stockSettingsBody } from "../shared/stock/testing";
import { fakeServer, NOON, ok, refusal, SHOP_BASE, SHOP_ID } from "../testing/fakeServer";
import { go } from "../testing/renderScreen";
import { PANEL_EXTENSION } from "./PanelRoot";
import { DesktopLayout } from "./tables";
import { CSRF, setWidth } from "./testing";

/**
 * The network between shops in the web panel (the expansion's module J): the same switch as in the
 * Mini App, the overview with its lists as the section's first screen, and real tables on a wide screen.
 */

const NOT_FOUND = refusal(404, "NOT_FOUND", "Topilmadi.");

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
});
afterEach(cleanup);

function backend(role: Role, networkOn: boolean) {
  return fakeServer((sent) => {
    switch (sent.path) {
      case "/api/v1/me/shops":
        return {
          status: 200,
          body: { items: [{ shop_id: SHOP_ID, name: "Baraka savdo", role, membership_id: "33333333-3333-4333-8333-333333333333" }], active_shop: SHOP_ID },
          headers: networkOn ? { "X-Qarz-Network": "on", "X-Qarz-Stock": "on" } : { "X-Qarz-Stock": "on" },
        };
      case `${SHOP_BASE}/overview`:
        return ok({ outstanding: 0, debtors: 0, overdue: { amount: 0, customers: 0 }, due_today: 0 });
      case `${SHOP_BASE}/overview/debtors`:
        return ok({ items: [], next_cursor: null });
      case `${SHOP_BASE}/stock/settings`:
        return ok(stockSettingsBody());
      case NETWORK:
        return ok(overviewBody({ links: [linkBody()], waiting: waitingBody({ notes: 1 }), ...(role === "owner" ? { invites: [] } : {}) }));
      case `${NETWORK}/notes/${NOTE_ID}`:
        return ok(noteBody());
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
const asked = (server: ReturnType<typeof fakeServer>) => server.sent.filter((sent) => sent.path.includes("/network"));

describe("the network in the web panel", () => {
  it("is absent while the switch is off: no section, its addresses are unknown routes, and nothing of it is asked", async () => {
    const server = backend("owner", false);
    await panel(server, 1280);
    // The stock is on here, and its sections are there: the network has a switch of its own.
    expect(hrefs()).toContain("#/stock");
    expect(hrefs().filter((href) => href?.startsWith("#/network"))).toEqual([]);
    expect(screen.queryByText("Hamkorlar")).toBeNull();
    for (const path of ["#/network", "#/network/payments", `#/network/notes/${NOTE_ID}`]) {
      go(path);
      expect(screen.getByRole("heading", { level: 1 }).textContent, path).toBe("Sahifa topilmadi");
    }
    expect(asked(server)).toEqual([]);
  });

  it("is absent for a seller with the switch on, and there for a manager after the stock's sections", async () => {
    const seller = backend("seller", true);
    await panel(seller, 1280);
    expect(hrefs()).toContain("#/stock");
    expect(hrefs()).not.toContain("#/network");
    go("#/network");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Sahifa topilmadi");
    expect(asked(seller)).toEqual([]);
    cleanup();
    window.location.hash = "";
    await panel(backend("manager", true), 1280);
    await waitFor(() => expect(hrefs()).toContain("#/network"));
    const side = [...new Set(hrefs())];
    expect(side.indexOf("#/network")).toBe(side.indexOf("#/suppliers") + 1);
  });

  it("opens on the overview: what awaits first, the section's lists beside it, and the links as a real table", async () => {
    const server = backend("owner", true);
    await panel(server, 1280);
    go("#/network");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Hamkorlar");
    const waiting = await screen.findByRole("region", { name: "Sizdan javob kutilmoqda" });
    expect(within(waiting).getByRole("link", { name: "Tasdiqlanmagan yuk xatlari" }).getAttribute("href")).toBe("#/network/notes/waiting");
    const tabs = within(screen.getByRole("navigation", { name: "Hamkorlar bo'limi" })).getAllByRole("link");
    expect(tabs.map((tab) => tab.getAttribute("href"))).toEqual(["#/network", "#/network/orders/out", "#/network/orders/in", "#/network/notes", "#/network/payments"]);
    const table = await screen.findByRole("table", { name: "Hamkorlar" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Hamkor", "Biz kimmiz", "Holati", "Kimdan kutilmoqda"]);
    expect(within(table).getByRole("link", { name: "Baraka ulgurji" }).getAttribute("href")).toBe(`#/network/links/${NET_LINK_ID}`);
    // The owner manages the links: the invitations are theirs to make.
    expect(screen.getByRole("button", { name: "Taklif kodi yaratish" })).toBeTruthy();
  });

  it("draws a delivery note's lines as a table, and keeps the section selected in the navigation", async () => {
    const server = backend("manager", true);
    await panel(server, 1280);
    go(`#/network/notes/${NOTE_ID}`);
    const table = await screen.findByRole("table", { name: "Yuk xati qatorlari" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Tovar", "Miqdor", "Narxi", "Jami"]);
    expect(document.querySelector('nav a[aria-current="page"]')?.getAttribute("href")).toBe("#/network");
  });
});
