// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  CUSTOMER_ID,
  customerBody,
  detailBody,
  fakeServer,
  itemBody,
  NO_OVERDUE,
  NOON,
  ok,
  refusal,
  type Reply,
  type Sent,
  settingsBody,
  SHOP_BASE,
  SHOP_ID,
} from "../testing/fakeServer";
import { go } from "../testing/renderScreen";
import type { ApiAuth } from "./api";
import type { Role } from "./navigation";
import { StaffRoot } from "./StaffRoot";

const OTHER_SHOP = "5a0c6d3e-0000-4000-8000-00000000bbbb";

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
});
afterEach(cleanup);

const bearer = async (): Promise<ApiAuth> => ({ kind: "bearer", token: "session-token" });
const heading = () => screen.getByRole("heading", { level: 1 }).textContent;
const shopName = () => screen.getByRole("banner").querySelector("strong")?.textContent;

const membership = (role: Role, shopId = SHOP_ID, name = "Baraka savdo") => ({ shop_id: shopId, name, role });

/** A server for one staff member: their shops, and a shop with one debtor. */
function backend(shops: unknown, extra: (sent: Sent) => Reply | null = () => null) {
  return fakeServer((sent) => {
    const special = extra(sent);
    if (special !== null) {
      return special;
    }
    if (sent.path === "/api/v1/me/shops") {
      return ok(shops);
    }
    if (sent.path === "/api/v1/me/active-shop") {
      return ok({ active_shop: (sent.body as { shop_id: string }).shop_id });
    }
    if (sent.path.endsWith("/overview")) {
      return ok({ outstanding: 120000, debtors: 1, overdue: { amount: 0, customers: 0 }, due_today: 0 });
    }
    if (sent.path.endsWith("/overview/debtors")) {
      return ok({ items: [{ ...customerBody(), overdue: NO_OVERDUE }], next_cursor: null });
    }
    if (sent.path.endsWith(`/customers/${CUSTOMER_ID}`)) {
      return ok(detailBody());
    }
    if (sent.path === `${SHOP_BASE}/catalog`) {
      return ok({ items: [itemBody()], next_cursor: null });
    }
    if (sent.path === SHOP_BASE) {
      return sent.method === "GET" ? ok(settingsBody()) : ok(settingsBody(sent.body as Record<string, unknown>));
    }
    return ok({ items: [customerBody()], next_cursor: null });
  });
}

function start(server: ReturnType<typeof fakeServer>, connect: () => Promise<ApiAuth | null> = bearer) {
  return render(
    <StaffRoot entryKey="entry.app" initialLanguage="uz" connect={connect} fetch={server.fetch} now={() => NOON} />,
  );
}

describe("opening the workspace", () => {
  it("signs in, finds the active shop, and shows its overview", async () => {
    const server = backend({ items: [membership("seller")], active_shop: SHOP_ID });
    start(server);
    expect(screen.getByRole("status").textContent).toBe("Yuklanmoqda…");
    expect(screen.queryByRole("navigation")).toBeNull();

    expect(await screen.findByText("Ali Valiyev")).toBeTruthy();
    expect(heading()).toBe("Umumiy ko'rinish");
    expect(shopName()).toBe("Baraka savdo");
    expect(server.sent[0]).toMatchObject({ method: "GET", path: "/api/v1/me/shops" });
    expect(server.sent.every((sent) => sent.headers["Authorization"] === "Bearer session-token")).toBe(true);
    expect(server.sent.some((sent) => sent.path === `${SHOP_BASE}/overview`)).toBe(true);
  });

  it("uses the only shop without asking, and does not change the active shop on the server", async () => {
    const server = backend({ items: [membership("owner")], active_shop: null });
    start(server);
    expect(await screen.findByText("Ali Valiyev")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Boshqa do'konga o'tish" })).toBeNull();
  });

  it("asks which shop when there are several and none is active, then remembers the choice", async () => {
    const server = backend({
      items: [membership("seller", SHOP_ID, "Baraka savdo"), membership("owner", OTHER_SHOP, "Ziyo market")],
      active_shop: null,
    });
    start(server);
    expect(await screen.findByRole("heading", { level: 1, name: "Do'konni tanlang" })).toBeTruthy();
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(server.sent.filter((sent) => sent.path.startsWith("/api/v1/shops/"))).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: /Ziyo market/ }));
    await waitFor(() => expect(shopName()).toBe("Ziyo market"));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "PUT", path: "/api/v1/me/active-shop", body: { shop_id: OTHER_SHOP } });
    // The role of the chosen shop decides the navigation: an owner there, a seller in the other.
    expect(within(screen.getByRole("navigation")).getByRole("link", { name: "Xodimlar" })).toBeTruthy();
    await waitFor(() =>
      expect(server.sent.some((sent) => sent.path === `/api/v1/shops/${OTHER_SHOP}/overview`)).toBe(true),
    );
  });

  it("lets someone with several shops switch from the overview", async () => {
    const server = backend({
      items: [membership("seller", SHOP_ID, "Baraka savdo"), membership("owner", OTHER_SHOP, "Ziyo market")],
      active_shop: SHOP_ID,
    });
    start(server);
    fireEvent.click(await screen.findByRole("button", { name: "Boshqa do'konga o'tish" }));
    fireEvent.click(screen.getByRole("button", { name: /Ziyo market/ }));
    await waitFor(() => expect(shopName()).toBe("Ziyo market"));
    expect(window.location.hash).toBe("#/");
  });

  it("shows the refusal when the chosen shop cannot be made active", async () => {
    const server = backend(
      { items: [membership("seller"), membership("owner", OTHER_SHOP, "Ziyo market")], active_shop: null },
      (sent) => (sent.method === "PUT" ? refusal(404, "NOT_FOUND", "Topilmadi.") : null),
    );
    start(server);
    fireEvent.click(await screen.findByRole("button", { name: /Ziyo market/ }));
    expect((await screen.findByRole("alert")).textContent).toContain("Topilmadi.");
    expect(heading()).toBe("Do'konni tanlang");
  });

  it("asks for the same shop again on 'try again', and opens it once the server lets it", async () => {
    let fail = true;
    const server = backend(
      { items: [membership("seller"), membership("owner", OTHER_SHOP, "Ziyo market")], active_shop: null },
      (sent) => (sent.method === "PUT" && fail ? "offline" : null),
    );
    start(server);
    // Before anything was chosen there is nothing to try again.
    await screen.findByRole("button", { name: /Ziyo market/ });
    expect(screen.queryByRole("button", { name: "Qayta urinish" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Ziyo market/ }));
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    fail = false;
    fireEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "Qayta urinish" }));
    await waitFor(() => expect(shopName()).toBe("Ziyo market"));
    expect(server.writes().map((sent) => sent.body)).toEqual([{ shop_id: OTHER_SHOP }, { shop_id: OTHER_SHOP }]);
  });

  it("says so when the person is in no shop", async () => {
    const server = backend({ items: [], active_shop: null });
    start(server);
    expect(await screen.findByRole("heading", { level: 1, name: "Do'kon yo'q" })).toBeTruthy();
    expect(screen.queryByRole("navigation")).toBeNull();
  });
});

describe("without a valid session", () => {
  it("asks to sign in when there is nothing to sign in with, and calls nothing", async () => {
    const server = backend({ items: [membership("owner")], active_shop: SHOP_ID });
    start(server, async () => null);
    expect(await screen.findByRole("heading", { level: 1, name: "Kirish talab qilinadi" })).toBeTruthy();
    expect(server.sent).toHaveLength(0);
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("asks to sign in when the server does not accept the session", async () => {
    const server = fakeServer(() => refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring."));
    start(server);
    expect(await screen.findByRole("heading", { level: 1, name: "Kirish talab qilinadi" })).toBeTruthy();
  });

  it("returns to the sign-in notice when the session ends while working", async () => {
    let expired = false;
    const server = backend({ items: [membership("seller")], active_shop: SHOP_ID }, () =>
      expired ? refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring.") : null,
    );
    start(server);
    await screen.findByText("Ali Valiyev");
    expired = true;
    go("#/customers");
    expect(await screen.findByRole("heading", { level: 1, name: "Kirish talab qilinadi" })).toBeTruthy();
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(screen.queryByText("Ali Valiyev")).toBeNull();
  });

  it("offers a retry when signing in fails for another reason", async () => {
    const server = backend({ items: [membership("seller")], active_shop: SHOP_ID });
    let attempt = 0;
    start(server, async () => {
      if (attempt++ === 0) {
        throw new TypeError("Failed to fetch");
      }
      return bearer();
    });
    fireEvent.click(await screen.findByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByText("Ali Valiyev")).toBeTruthy();
  });
});

describe("routes of the workspace", () => {
  it("opens a customer inside the Customers section and keeps that tab marked", async () => {
    window.location.hash = `#/customers/${CUSTOMER_ID}`;
    const server = backend({ items: [membership("seller")], active_shop: SHOP_ID });
    start(server);
    expect(await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" })).toBeTruthy();
    expect(heading()).toBe("Mijoz");
    const current = within(screen.getByRole("navigation"))
      .getAllByRole("link")
      .filter((link) => link.getAttribute("aria-current") === "page");
    expect(current.map((link) => link.textContent)).toEqual(["Mijozlar"]);

    go(`#/customers/${CUSTOMER_ID}/payment`);
    expect(heading()).toBe("To'lov qabul qilish");
    expect(await screen.findByLabelText("Summa, so'm")).toBeTruthy();

    go("#/new");
    expect(heading()).toBe("Yangi yozuv");
    expect(await screen.findByRole("link", { name: "Nasiya" })).toBeTruthy();

    go("#/customers/new");
    expect(heading()).toBe("Yangi mijoz");
    expect(screen.getByLabelText("Ism")).toBeTruthy();
  });

  it("treats anything that is not a customer identifier as an unknown page and asks the server nothing", async () => {
    const server = backend({ items: [membership("owner")], active_shop: SHOP_ID });
    start(server);
    await screen.findByText("Ali Valiyev");
    const before = server.sent.length;
    for (const hash of ["#/customers/42", "#/customers/../overview", `#/customers/${CUSTOMER_ID}/refund`, "#/customers/new/credit"]) {
      go(hash);
      expect(heading()).toBe("Sahifa topilmadi");
    }
    expect(server.sent).toHaveLength(before);
  });

  it("still keeps a seller out of the manager sections", async () => {
    const server = backend({ items: [membership("seller")], active_shop: SHOP_ID });
    start(server);
    await screen.findByText("Ali Valiyev");
    go("#/reports");
    expect(heading()).toBe("Sahifa topilmadi");
    go("#/staff");
    expect(heading()).toBe("Sahifa topilmadi");
  });
});

describe("catalog, goods and settings in the workspace", () => {
  const navLinks = () => within(screen.getByRole("navigation")).getAllByRole("link").map((link) => link.textContent);

  it("lets a seller read the catalog from the tab bar, with nothing to change it", async () => {
    const server = backend({ items: [membership("seller")], active_shop: SHOP_ID });
    start(server);
    await screen.findByText("Ali Valiyev");
    expect(navLinks()).toEqual(["Umumiy ko'rinish", "Mijozlar", "Yangi yozuv", "Katalog"]);
    go("#/catalog");
    expect(heading()).toBe("Katalog");
    expect(await screen.findByText("Non")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Yangi mahsulot" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Tahrirlash" })).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });

  it("gives a manager the catalog controls", async () => {
    const server = backend({ items: [membership("manager")], active_shop: SHOP_ID });
    start(server);
    await screen.findByText("Ali Valiyev");
    go("#/catalog");
    await screen.findByText("Non");
    expect(screen.getByRole("button", { name: "Yangi mahsulot" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Tahrirlash" })).toBeTruthy();
  });

  it("keeps a seller out of the shop settings without asking the server for them", async () => {
    const server = backend({ items: [membership("seller")], active_shop: SHOP_ID });
    start(server);
    await screen.findByText("Ali Valiyev");
    go("#/shop-settings");
    expect(heading()).toBe("Sahifa topilmadi");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent.some((sent) => sent.path === SHOP_BASE)).toBe(false);
    expect(navLinks()).not.toContain("Do'kon sozlamalari");
  });

  it("shows a manager the shop settings to read", async () => {
    const manager = backend({ items: [membership("manager")], active_shop: SHOP_ID });
    start(manager);
    await screen.findByText("Ali Valiyev");
    go("#/shop-settings");
    expect(heading()).toBe("Do'kon sozlamalari");
    expect(await screen.findByText("Sozlamalarni faqat do'kon egasi o'zgartira oladi.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Saqlash" })).toBeNull();
    expect(manager.writes()).toHaveLength(0);
  });

  it("gives the owner a form that changes the shop settings", async () => {
    const owner = backend({ items: [membership("owner")], active_shop: SHOP_ID });
    start(owner);
    await screen.findByText("Ali Valiyev");
    go("#/shop-settings");
    fireEvent.change(await screen.findByLabelText("Odatdagi to'lash muddati, kun"), { target: { value: "14" } });
    fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
    expect(await screen.findByText("Sozlamalar saqlandi.")).toBeTruthy();
    expect(owner.writes()[0]).toMatchObject({ method: "PATCH", path: SHOP_BASE, body: { default_promise_days: 14 } });
  });

  it("opens the add-goods screen from the customer page, inside the customers section", async () => {
    const server = backend({ items: [membership("seller")], active_shop: SHOP_ID });
    start(server);
    await screen.findByText("Ali Valiyev");
    go(`#/customers/${CUSTOMER_ID}`);
    const link = await screen.findByRole("link", { name: "Tovarlarni qo'shish" });
    const entryId = "22222222-2222-4222-8222-222222222222";
    expect(link.getAttribute("href")).toBe(`#/customers/${CUSTOMER_ID}/entries/${entryId}/goods`);
    go(`#/customers/${CUSTOMER_ID}/entries/${entryId}/goods`);
    expect(heading()).toBe("Yozuvga tovar qo'shish");
    expect(await screen.findByRole("button", { name: "Tovarlarni saqlash" })).toBeTruthy();
    const current = within(screen.getByRole("navigation"))
      .getAllByRole("link")
      .filter((candidate) => candidate.getAttribute("aria-current") === "page");
    expect(current.map((candidate) => candidate.textContent)).toEqual(["Mijozlar"]);
  });
});

describe("what the workspace offers when the server keeps permissions per member", () => {
  const navLinks = () => within(screen.getByRole("navigation")).getAllByRole("link").map((link) => link.textContent);
  const MINE = `${SHOP_BASE}/permissions/mine`;

  /** The person's shops with the header the server sends while the matrix is on, and what they hold. */
  function withPermissions(role: Role, held: string[] | "broken") {
    return backend({}, (sent) => {
      if (sent.path === "/api/v1/me/shops") {
        return {
          status: 200,
          body: { items: [membership(role)], active_shop: SHOP_ID },
          headers: { "X-Qarz-Permissions": "on" },
        };
      }
      if (sent.path === MINE) {
        return held === "broken" ? refusal(503, "TIMEOUT", "") : ok({ membership_id: "m-1", role, permissions: held });
      }
      return null;
    });
  }

  it("asks nothing about permissions while the server does not say it keeps them", async () => {
    const server = backend({ items: [membership("seller")], active_shop: SHOP_ID });
    start(server);
    await screen.findByText("Ali Valiyev");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent.filter((sent) => sent.path === MINE)).toHaveLength(0);
    expect(navLinks()).toEqual(["Umumiy ko'rinish", "Mijozlar", "Yangi yozuv", "Katalog"]);
  });

  it("opens for a seller what the owner granted, and nothing the seller was not given", async () => {
    const server = withPermissions("seller", ["ledger.view", "credits.record", "reports.view"]);
    start(server);
    await screen.findByText("Ali Valiyev");
    await waitFor(() => expect(navLinks()).toContain("Hisobotlar"));
    expect(server.sent.filter((sent) => sent.path === MINE)).toHaveLength(1);
    go("#/staff");
    expect(heading()).toBe("Sahifa topilmadi");
    go("#/disputes");
    expect(heading()).toBe("Sahifa topilmadi");
  });

  it("closes for a manager what the owner denied, and asks the server nothing for it", async () => {
    const server = withPermissions("manager", ["ledger.view", "credits.record", "payments.record"]);
    start(server);
    await screen.findByText("Ali Valiyev");
    await waitFor(() => expect(navLinks()).not.toContain("Hisobotlar"));
    const before = server.sent.length;
    go("#/reports");
    expect(heading()).toBe("Sahifa topilmadi");
    go("#/catalog");
    expect(await screen.findByText("Non")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Yangi mahsulot" })).toBeNull();
    expect(server.sent.slice(before).some((sent) => sent.path.includes("/reports"))).toBe(false);
  });

  it("keeps to the role when the member's permissions cannot be read", async () => {
    const server = withPermissions("seller", "broken");
    start(server);
    await screen.findByText("Ali Valiyev");
    await waitFor(() => expect(server.sent.some((sent) => sent.path === MINE)).toBe(true));
    expect(navLinks()).toEqual(["Umumiy ko'rinish", "Mijozlar", "Yangi yozuv", "Katalog"]);
  });
});

describe("the cash book in the workspace", () => {
  const navLinks = () => within(screen.getByRole("navigation")).getAllByRole("link").map((link) => link.textContent);

  /** The person's shops, with the header the server sends while the cash book is on. */
  function withCashBook(role: Role) {
    return backend({}, (sent) => {
      if (sent.path === "/api/v1/me/shops") {
        return {
          status: 200,
          body: { items: [membership(role)], active_shop: SHOP_ID },
          headers: { "X-Qarz-Cash-Book": "on" },
        };
      }
      if (sent.path === `${SHOP_BASE}/cash/categories`) {
        return ok({ items: [], currencies: ["UZS"] });
      }
      if (sent.path === `${SHOP_BASE}/cash/day`) {
        return ok({ date: "2026-10-06", balances: [], totals: [], entries: [], next_cursor: null });
      }
      return null;
    });
  }

  it("is not offered, and nothing of it is asked, while the server does not say it is on", async () => {
    const server = backend({ items: [membership("owner")], active_shop: SHOP_ID });
    start(server);
    await screen.findByText("Ali Valiyev");
    expect(navLinks()).not.toContain("Kassa");
    go("#/cash");
    expect(heading()).toBe("Sahifa topilmadi");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent.filter((sent) => sent.path.includes("/cash"))).toHaveLength(0);
  });

  it("is a section of a manager's workspace once the server says it is on", async () => {
    const server = withCashBook("manager");
    start(server);
    await screen.findByText("Ali Valiyev");
    expect(navLinks()).toContain("Kassa");
    go("#/cash");
    expect(heading()).toBe("Kassa");
    expect(await screen.findByRole("heading", { name: "Kassa: 2026-yil 6-oktabr" })).toBeTruthy();
  });

  it("is not a section of a seller's workspace even then", async () => {
    const server = withCashBook("seller");
    start(server);
    await screen.findByText("Ali Valiyev");
    expect(navLinks()).toEqual(["Umumiy ko'rinish", "Mijozlar", "Yangi yozuv", "Katalog"]);
    go("#/cash");
    expect(heading()).toBe("Sahifa topilmadi");
    expect(server.sent.filter((sent) => sent.path.includes("/cash"))).toHaveLength(0);
  });
});
