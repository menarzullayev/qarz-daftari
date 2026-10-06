// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  accountBody,
  CUSTOMER_ID,
  customerBody,
  detailBody,
  entryBody,
  fakeServer,
  linkBody,
  LINK_ID,
  ME_BASE,
  NO_OVERDUE,
  NOON,
  ok,
  openDisputeBody,
  refusal,
  type Reply,
  type Sent,
  SHOP_BASE,
  SHOP_ID,
} from "../testing/fakeServer";
import { go } from "../testing/renderScreen";
import type { ApiAuth } from "./api";
import { matchCustomerRoute } from "./customer/CustomerArea";
import { isCustomerPath } from "./customer/paths";
import { StaffRoot } from "./StaffRoot";

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
});
afterEach(cleanup);

const OTHER_LINK = "77777777-7777-4777-8777-777777777778";
const bearer = async (): Promise<ApiAuth> => ({ kind: "bearer", token: "session-token" });
const heading = () => screen.getByRole("heading", { level: 1 }).textContent;
const navLinks = () => within(screen.getByRole("navigation", { name: "Asosiy menyu" })).getAllByRole("link").map((link) => link.textContent);

const BARAKA = { link_id: LINK_ID, shop_name: "Baraka savdo", display_name: "Ali Valiyev", balance: 120000 };
const OLTIN = { link_id: OTHER_LINK, shop_name: "Oltin don", display_name: "Ali aka", balance: 0 };
const STAFF = { items: [{ shop_id: SHOP_ID, name: "Mening do'konim", role: "seller", membership_id: "m-1" }], active_shop: SHOP_ID };
const NO_SHOPS = { items: [], active_shop: null };

/** A person with `shops` and with `accounts()` as a customer. */
function backend(shops: unknown, accounts: () => Reply, extra: (sent: Sent) => Reply | null = () => null) {
  return fakeServer((sent) => {
    const special = extra(sent);
    if (special !== null) {
      return special;
    }
    if (sent.path === "/api/v1/me/shops") {
      return ok(shops);
    }
    if (sent.path === "/api/v1/me/accounts") {
      return accounts();
    }
    if (sent.path.startsWith("/api/v1/me/accounts/")) {
      const linkId = sent.path.split("/")[5];
      return sent.method === "GET"
        ? ok(accountBody(linkId === OTHER_LINK ? { link_id: OTHER_LINK, shop_name: "Oltin don", balance: 0, entries: [], entries_total: 0 } : {}))
        : ok({ disconnected: true });
    }
    if (sent.path.endsWith("/overview")) {
      return ok({ outstanding: 0, debtors: 0, overdue: { amount: 0, customers: 0 }, due_today: 0 });
    }
    if (sent.path.endsWith("/overview/debtors")) {
      return ok({ items: [{ ...customerBody({ display_name: "Do'kon mijozi" }), overdue: NO_OVERDUE }], next_cursor: null });
    }
    return ok({ items: [], next_cursor: null });
  });
}

function start(server: ReturnType<typeof fakeServer>, customerPage = true) {
  return render(
    <StaffRoot
      entryKey="entry.app"
      initialLanguage="uz"
      connect={bearer}
      fetch={server.fetch}
      now={() => NOON}
      customerPage={customerPage}
    />,
  );
}

describe("which paths are the customer's own", () => {
  it("only /my and what is under it", () => {
    expect(["/my", `/my/${LINK_ID}`, "/my/anything"].map(isCustomerPath)).toEqual([true, true, true]);
    expect(["/", "/mystery", "/customers", "/customers/my", ""].map(isCustomerPath)).toEqual([false, false, false, false, false]);
  });

  it("matches the list and one account, and nothing else", () => {
    expect(matchCustomerRoute("/my", true)).toEqual({ screen: "accounts" });
    expect(matchCustomerRoute(`/my/${LINK_ID}`, true)).toEqual({ screen: "account", linkId: LINK_ID });
    // The home route is the list only for a person who has no shop to go home to.
    expect(matchCustomerRoute("/", false)).toEqual({ screen: "accounts" });
    expect(matchCustomerRoute("/", true)).toBeNull();
    for (const path of ["/my/42", `/my/${LINK_ID}/disputes`, `/my/${LINK_ID}x`, "/my/..%2Fshops", "/customers"]) {
      expect(matchCustomerRoute(path, false)).toBeNull();
    }
  });
});

describe("a person who is only a customer", () => {
  it("sees their one account at once, without staff navigation", async () => {
    const server = backend(NO_SHOPS, () => ok({ items: [BARAKA] }));
    start(server);
    expect(await screen.findByRole("heading", { level: 2, name: "Baraka savdo" })).toBeTruthy();
    expect(heading()).toBe("Mening qarzlarim");
    expect(screen.getByRole("banner").textContent).toContain("Mening hisobim");
    expect(screen.getByRole("banner").textContent).not.toContain("Xodimlar ish joyi");
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(server.sent.some((sent) => sent.path === ME_BASE)).toBe(true);
    // Nothing of any shop's staff API is asked for.
    expect(server.sent.filter((sent) => sent.path.startsWith("/api/v1/shops"))).toEqual([]);
    expect(server.sent.every((sent) => sent.headers["Authorization"] === "Bearer session-token")).toBe(true);
  });

  it("chooses between several accounts by shop name and what is owed there", async () => {
    const server = backend(NO_SHOPS, () => ok({ items: [BARAKA, OLTIN] }));
    start(server);
    const first = await screen.findByRole("link", { name: /Baraka savdo/ });
    expect(first.textContent).toContain("120 000 so'm");
    expect(first.getAttribute("href")).toBe(`#/my/${LINK_ID}`);
    const second = screen.getByRole("link", { name: /Oltin don/ });
    expect(second.textContent).toContain("0 so'm");
    // The list is the server's answer and nothing more: no account is read until one is opened.
    expect(server.sent.filter((sent) => sent.path.startsWith("/api/v1/me/accounts/"))).toEqual([]);

    go(`#/my/${OTHER_LINK}`);
    expect(await screen.findByRole("heading", { level: 2, name: "Oltin don" })).toBeTruthy();
    expect(heading()).toBe("Do'kondagi hisobim");
    expect(server.sent.at(-1)).toMatchObject({ method: "GET", path: `/api/v1/me/accounts/${OTHER_LINK}` });
  });

  it("shows not found for a path that is neither the list nor an account, and asks the server nothing", async () => {
    const server = backend(NO_SHOPS, () => ok({ items: [BARAKA, OLTIN] }));
    start(server);
    await screen.findByRole("link", { name: /Baraka savdo/ });
    const before = server.sent.length;
    go("#/my/not-a-link");
    expect(heading()).toBe("Sahifa topilmadi");
    go("#/customers");
    expect(heading()).toBe("Sahifa topilmadi");
    expect(server.sent).toHaveLength(before);
  });

  it("goes back to an empty list after disconnecting from the only shop", async () => {
    let connected = true;
    const server = backend(NO_SHOPS, () => ok({ items: connected ? [BARAKA] : [] }), (sent) => {
      if (sent.method === "POST" && sent.path === `${ME_BASE}/disconnect`) {
        connected = false;
        return ok({ disconnected: true });
      }
      return null;
    });
    start(server);
    fireEvent.click(await screen.findByRole("button", { name: "Do'kondan uzilish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, uzilaman" }));
    await screen.findByText(/Do'kondan uzildingiz/);
    fireEvent.click(screen.getByRole("button", { name: "Hisoblar ro'yxati" }));
    expect(await screen.findByText("Siz hozir hech bir do'konga mijoz sifatida ulanmagansiz.")).toBeTruthy();
  });

  it("shows the failure and retries when the accounts cannot be read", async () => {
    let fail = true;
    const server = backend(NO_SHOPS, () => (fail ? "offline" : ok({ items: [BARAKA] })));
    start(server);
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi.");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Baraka savdo" })).toBeTruthy();
  });

  it("sees the usual notice when they are neither staff nor a customer", async () => {
    start(backend(NO_SHOPS, () => ok({ items: [] })));
    expect(await screen.findByRole("heading", { level: 1, name: "Do'kon yo'q" })).toBeTruthy();
  });
});

describe("a person who works in one shop and owes in another", () => {
  it("opens on the staff workspace and offers the way to their own debts", async () => {
    const server = backend(STAFF, () => ok({ items: [BARAKA] }));
    start(server);
    expect(await screen.findByText("Do'kon mijozi")).toBeTruthy();
    expect(heading()).toBe("Umumiy ko'rinish");
    expect(screen.getByRole("banner").textContent).toContain("Xodimlar ish joyi");
    expect(screen.getByRole("link", { name: "Mening qarzlarim" }).getAttribute("href")).toBe("#/my");
    // Their own account is not read until they go there.
    expect(server.sent.filter((sent) => sent.path === ME_BASE)).toEqual([]);
  });

  it("switches to their own account and back by the address", async () => {
    const server = backend(STAFF, () => ok({ items: [BARAKA] }));
    start(server);
    await screen.findByText("Do'kon mijozi");

    go("#/my");
    expect(await screen.findByRole("heading", { level: 2, name: "Baraka savdo" })).toBeTruthy();
    expect(heading()).toBe("Mening qarzlarim");
    expect(screen.queryByText("Do'kon mijozi")).toBeNull();
    expect(navLinks()).toEqual(["Do'konim", "Mening qarzlarim"]);
    const mine = screen.getByRole("link", { name: "Mening qarzlarim" });
    expect(mine.getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Do'konim" }).getAttribute("href")).toBe("#/");

    go("#/");
    expect(await screen.findByText("Do'kon mijozi")).toBeTruthy();
    expect(heading()).toBe("Umumiy ko'rinish");
    expect(screen.queryByRole("heading", { level: 2, name: "Baraka savdo" })).toBeNull();
  });

  it("keeps the two sides apart: the staff side never shows the customer's own account", async () => {
    const server = backend(STAFF, () => ok({ items: [BARAKA] }));
    start(server);
    await screen.findByText("Do'kon mijozi");
    go("#/customers");
    await waitFor(() => expect(heading()).toBe("Mijozlar"));
    expect(document.body.textContent).not.toContain("Baraka savdo");
    expect(server.sent.filter((sent) => sent.path.startsWith("/api/v1/me/accounts/"))).toEqual([]);
  });

  it("offers no way to debts for staff who are nobody's customer, and /my is not a page for them", async () => {
    const server = backend(STAFF, () => ok({ items: [] }));
    start(server);
    await screen.findByText("Do'kon mijozi");
    expect(screen.queryByRole("link", { name: "Mening qarzlarim" })).toBeNull();
    go("#/my");
    expect(heading()).toBe("Sahifa topilmadi");
  });

  it("offers staff of two shops who are nobody's customer the shop switch and no debts", async () => {
    const two = {
      items: [...STAFF.items, { shop_id: "5a0c6d3e-0000-4000-8000-00000000bbbb", name: "Ikkinchi do'kon", role: "owner", membership_id: "m-9" }],
      active_shop: SHOP_ID,
    };
    start(backend(two, () => ok({ items: [] })));
    await screen.findByText("Do'kon mijozi");
    expect(screen.getByRole("button", { name: "Boshqa do'konga o'tish" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Mening qarzlarim" })).toBeNull();
  });

  it("offers staff of two shops who are also a customer both the shop switch and their debts", async () => {
    const two = {
      items: [...STAFF.items, { shop_id: "5a0c6d3e-0000-4000-8000-00000000bbbb", name: "Ikkinchi do'kon", role: "owner", membership_id: "m-9" }],
      active_shop: SHOP_ID,
    };
    start(backend(two, () => ok({ items: [BARAKA] })));
    await screen.findByText("Do'kon mijozi");
    expect(screen.getByRole("button", { name: "Boshqa do'konga o'tish" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Mening qarzlarim" })).toBeTruthy();
  });

  it("lets staff work when their accounts cannot be read: the debts are just not offered", async () => {
    const server = backend(STAFF, () => refusal(500, "ERROR", "Xatolik yuz berdi."));
    start(server);
    expect(await screen.findByText("Do'kon mijozi")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Mening qarzlarim" })).toBeNull();
  });

  it("knows which membership is theirs: a seller is offered goods on their own sale only", async () => {
    const MINE = "22222222-2222-4222-8222-22222222aaaa";
    const THEIRS = "22222222-2222-4222-8222-22222222bbbb";
    const server = backend(STAFF, () => ok({ items: [] }), (sent) => {
      if (sent.path === `${SHOP_BASE}/customers/${CUSTOMER_ID}/link`) {
        return ok(linkBody());
      }
      if (sent.path === `${SHOP_BASE}/customers/${CUSTOMER_ID}`) {
        return ok(
          detailBody({
            entries: [entryBody({ id: MINE, seq: 2, author_id: "m-1" }), entryBody({ id: THEIRS, seq: 1, author_id: "m-2" })],
            entries_total: 2,
          }),
        );
      }
      return null;
    });
    start(server);
    await screen.findByText("Do'kon mijozi");
    go(`#/customers/${CUSTOMER_ID}`);
    await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
    const offered = screen.getAllByRole("link", { name: "Tovarlarni qo'shish" }).map((link) => link.getAttribute("href"));
    expect(offered).toEqual([`#/customers/${CUSTOMER_ID}/entries/${MINE}/goods`]);
  });
});

describe("the web panel", () => {
  it("is for staff only: it does not ask for customer accounts and has no customer page", async () => {
    const server = backend(STAFF, () => ok({ items: [BARAKA] }));
    start(server, false);
    await screen.findByText("Do'kon mijozi");
    expect(server.sent.filter((sent) => sent.path.startsWith("/api/v1/me/accounts"))).toEqual([]);
    expect(screen.queryByRole("link", { name: "Mening qarzlarim" })).toBeNull();
    go("#/my");
    expect(heading()).toBe("Sahifa topilmadi");
  });
});

describe("the staff screens for connecting customers and for disputes, by role", () => {
  const staff = (role: string) => ({
    items: [{ shop_id: SHOP_ID, name: "Mening do'konim", role, membership_id: "m-1" }],
    active_shop: SHOP_ID,
  });
  const shop = (role: string) =>
    backend(staff(role), () => ok({ items: [] }), (sent) => {
      if (sent.path === `${SHOP_BASE}/disputes`) {
        return role === "seller"
          ? refusal(403, "FORBIDDEN_ROLE", "Bu amal uchun sizning rolingiz yetarli emas.")
          : ok({ items: [openDisputeBody()] });
      }
      if (sent.path === `${SHOP_BASE}/waiting`) {
        return ok({ items: [{ id: OTHER_LINK, name: "Vali T.", since: "2026-10-06T05:10:00+00:00" }] });
      }
      if (sent.path === `${SHOP_BASE}/counter-code`) {
        return ok({ exists: true, since: "2026-09-01T05:00:00+00:00" });
      }
      return null;
    });

  it("does not show a seller the list of disputes, nor ask the server for it", async () => {
    const server = shop("seller");
    start(server);
    await screen.findByText("Do'kon mijozi");
    go("#/disputes");
    expect(heading()).toBe("Sahifa topilmadi");
    expect(screen.queryByText("Ali Valiyev")).toBeNull();
    expect(server.sent.filter((sent) => sent.path === `${SHOP_BASE}/disputes`)).toEqual([]);
    expect(screen.queryByRole("link", { name: "E'tirozlar va so'rovlar" })).toBeNull();
  });

  it.each(["manager", "owner"])("shows a %s the open disputes", async (role) => {
    start(shop(role));
    await screen.findByText("Do'kon mijozi");
    go("#/disputes");
    expect(await screen.findByText("Mijoz sababi: Men bu tovarni olmaganman")).toBeTruthy();
    expect(heading()).toBe("E'tirozlar");
  });

  it.each(["seller", "manager", "owner"])("shows a %s the waiting list and the counter code inside Customers", async (role) => {
    start(shop(role));
    await screen.findByText("Do'kon mijozi");
    go("#/customers/waiting");
    expect(await screen.findByText("Vali T.")).toBeTruthy();
    expect(heading()).toBe("Ulanishni kutayotganlar");
    expect(screen.getByRole("link", { name: "Mijozlar" }).getAttribute("aria-current")).toBe("page");

    go("#/customers/counter-code");
    expect(await screen.findByText(/Peshtaxta kodi bor/)).toBeTruthy();
    expect(heading()).toBe("Peshtaxta kodi");
    expect(screen.queryByRole("button", { name: "Kodni almashtirish" }) !== null).toBe(role !== "seller");
  });
});
