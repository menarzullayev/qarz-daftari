// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { fakeServer, NOON, ok, refusal, SHOP_BASE, SHOP_ID } from "../testing/fakeServer";
import { go } from "../testing/renderScreen";
import type { ApiAuth } from "./api";
import { type Role } from "./navigation";
import { linkBody, NET_LINK_ID, NETWORK, NOTE_ID, noteBody, ORDER_ID, orderBody, overviewBody, PAYMENT_ID, paymentBody, waitingBody } from "./network/testing";
import { StaffRoot } from "./StaffRoot";
import { stockSettingsBody } from "./stock/testing";

const ON = { "X-Qarz-Network": "on", "X-Qarz-Stock": "on" };

/**
 * The network between shops in the Telegram Mini App, behind its platform switch (the expansion's
 * module J). The server says that the network is on with a header of the person's shops; without that
 * header nothing of it is offered, its addresses are unknown routes, and nothing of it is asked. With
 * it, the section opens for a member who may see the network.
 */

const NOT_FOUND = refusal(404, "NOT_FOUND", "Topilmadi.");

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
});
afterEach(cleanup);

const bearer = async (): Promise<ApiAuth> => ({ kind: "bearer", token: "session-token" });

function backend(role: Role, header: Record<string, string>, options: { permissions?: string[]; overview?: unknown } = {}) {
  const items = [{ shop_id: SHOP_ID, name: "Baraka savdo", role, membership_id: "33333333-3333-4333-8333-333333333333" }];
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      return sent.path === `${NETWORK}/payments/${PAYMENT_ID}/confirm` ? ok(paymentBody({ status: "confirmed", in_own_books: true })) : NOT_FOUND;
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
      case NETWORK:
        return ok(options.overview ?? overviewBody());
      case `${NETWORK}/orders`:
        return ok({ orders: sent.query["status"] === "sent" ? [orderBody({ lines: undefined, events: undefined })] : [], next_cursor: null });
      case `${NETWORK}/notes`:
        return ok({ notes: [noteBody({ lines: undefined, events: undefined })], next_cursor: null });
      case `${NETWORK}/payments`:
        return ok({ payments: [paymentBody(), paymentBody({ id: "e5e5e5e5-e5e5-4e5e-8e5e-e5e5e5e5e5e6", recorded_by: "own", in_own_books: true })], next_cursor: null });
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

const asked = (server: ReturnType<typeof fakeServer>) => server.sent.filter((sent) => sent.path.includes("/network"));
const heading = () => screen.getByRole("heading", { level: 1 }).textContent;
/** Every place the section could be reached from: the navigation, and the "More" screen's list. */
const offered = () => [...new Set([...document.querySelectorAll("a")].map((link) => link.getAttribute("href")).filter((href) => href?.startsWith("#/network")))];

describe("the network switched off", () => {
  it("offers nothing of it to an owner: no entry in the navigation or under More, and its addresses are unknown routes", async () => {
    const server = backend("owner", {});
    await miniApp(server);
    expect(offered()).toEqual([]);
    go("#/more");
    expect(offered()).toEqual([]);
    expect(screen.queryByText("Hamkorlar")).toBeNull();
    for (const path of ["#/network", "#/network/orders/new", "#/network/notes", `#/network/links/${NET_LINK_ID}`, `#/network/notes/${NOTE_ID}`]) {
      go(path);
      expect(heading(), path).toBe("Sahifa topilmadi");
    }
    // Not one request of the network was made, and none of its module was run.
    expect(asked(server)).toEqual([]);
  });

  it.each([
    ["off", { "X-Qarz-Network": "off" }],
    ["true", { "X-Qarz-Network": "true" }],
    ["empty", { "X-Qarz-Network": "" }],
    ["the stock's, not the network's", { "X-Qarz-Stock": "on" }],
    ["the cash book's", { "X-Qarz-Cash-Book": "on" }],
  ])("is also what a member sees when the header is %s: only the word 'on' turns it on", async (_name, header) => {
    const server = backend("owner", header);
    await miniApp(server);
    go("#/more");
    expect(offered()).toEqual([]);
    go("#/network");
    expect(heading()).toBe("Sahifa topilmadi");
    expect(asked(server)).toEqual([]);
    go("#/");
    expect(heading()).toBe("Umumiy ko'rinish");
  });
});

describe("the network switched on", () => {
  it("is still absent for a seller, who may not see it, and for a manager it was taken from", async () => {
    const seller = backend("seller", ON);
    await miniApp(seller);
    go("#/more");
    expect(offered()).toEqual([]);
    go("#/network");
    expect(heading()).toBe("Sahifa topilmadi");
    expect(asked(seller)).toEqual([]);
    cleanup();
    window.location.hash = "";
    const manager = backend("manager", ON, { permissions: ["ledger.view", "stock.view", "reports.view"] });
    await miniApp(manager);
    go("#/network");
    expect(heading()).toBe("Sahifa topilmadi");
    expect(asked(manager)).toEqual([]);
  });

  it("gives a manager the section under More, and its short screen: what awaits an answer comes first", async () => {
    const server = backend("manager", ON, { overview: overviewBody({ waiting: waitingBody({ orders: 1, notes: 1, payments: 1 }) }) });
    await miniApp(server);
    go("#/more");
    expect(offered()).toEqual(["#/network"]);
    go("#/network");
    expect(heading()).toBe("Hamkorlar");
    const waiting = await screen.findByRole("region", { name: "Sizdan javob kutilmoqda" });
    expect([...waiting.querySelectorAll(".figure")].map((figure) => figure.textContent)).toEqual([
      "Javob berilmagan buyurtmalar1",
      "Tasdiqlanmagan yuk xatlari1",
      "Javob kutayotgan to'lovlar1",
    ]);
    // It is the first thing of the screen.
    expect(document.querySelector("#main-content, main")?.querySelector("section")).toBe(waiting);
    const orders = within(await screen.findByRole("list", { name: "Kelgan buyurtmalar" }));
    expect(orders.getByRole("link", { name: "Buyurtma № 12" }).getAttribute("href")).toBe(`#/network/orders/${ORDER_ID}`);
    const notes = within(await screen.findByRole("list", { name: "Yuk xatlari" }));
    expect(notes.getByRole("link", { name: "Yuk xati № 5" }).getAttribute("href")).toBe(`#/network/notes/${NOTE_ID}`);
    // Only what the partner recorded awaits this shop; its own payment awaits the partner.
    const payments = within(await screen.findByRole("list", { name: "Tasdig'ingizni kutayotgan to'lovlar" }));
    expect(payments.getAllByRole("listitem")).toHaveLength(1);
    // Each list was asked for what awaits, and the lists of nothing were not asked at all.
    expect(asked(server).filter((sent) => sent.path === `${NETWORK}/orders`).map((sent) => sent.query)).toEqual([{ role: "supplier", status: "sent" }]);
    expect(asked(server).filter((sent) => sent.path === `${NETWORK}/notes`).map((sent) => sent.query)).toEqual([{ role: "buyer", status: "issued" }]);
    expect(asked(server).filter((sent) => sent.path === `${NETWORK}/payments`).map((sent) => sent.query)).toEqual([{ status: "awaiting" }]);
  });

  it("answers a payment from the short screen with one request, and offers a new order to one who buys", async () => {
    const server = backend("manager", ON, { overview: overviewBody({ links: [linkBody(), linkBody({ id: "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a2", role: "supplier" })], waiting: waitingBody({ payments: 1 }) }) });
    await miniApp(server, "#/network");
    expect((await screen.findByRole("link", { name: "Yangi buyurtma" })).getAttribute("href")).toBe("#/network/orders/new");
    const payments = within(await screen.findByRole("list", { name: "Tasdig'ingizni kutayotgan to'lovlar" }));
    fireEvent.click(payments.getByRole("button", { name: "To'lovni tasdiqlash" }));
    expect(screen.getByText("Tasdiqlasangiz, to'lov sizning mijoz hisobingizga yoziladi.")).toBeTruthy();
    expect(server.writes()).toEqual([]);
    fireEvent.click(within(screen.getByRole("group", { name: "To'lovni tasdiqlash" })).getByRole("button", { name: "To'lovni tasdiqlash" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${NETWORK}/payments/${PAYMENT_ID}/confirm`, body: {} });
  });

  it("offers nothing to answer with to a member who only reads the network", async () => {
    const server = backend("seller", ON, {
      permissions: ["ledger.view", "network.view"],
      overview: overviewBody({ links: [linkBody({ state: "requested", invited: true })], waiting: waitingBody({ links: 1, payments: 1 }) }),
    });
    await miniApp(server, "#/network");
    await screen.findByRole("list", { name: "Tasdig'ingizni kutayotgan to'lovlar" });
    expect(screen.queryByRole("button", { name: "To'lovni tasdiqlash" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Qabul qilish" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Yangi buyurtma" })).toBeNull();
  });
});
