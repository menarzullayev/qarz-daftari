// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Role } from "../shared/navigation";
import {
  creditSettingsBody,
  CUSTOMER_ID,
  customerBody,
  detailBody,
  fakeServer,
  itemBody,
  linkBody,
  NO_OVERDUE,
  NOON,
  ok,
  refusal,
  type Reply,
  type Sent,
  settingsBody,
  SHOP_BASE,
  SHOP_ID,
  subscriptionBody,
} from "../testing/fakeServer";
import { go } from "../testing/renderScreen";
import { type LoginReturn, NO_RETURN } from "./loginReturn";
import { PanelRoot } from "./PanelRoot";
import type { LoginWidgetProps } from "./TelegramLogin";
import { CSRF, MANAGER_ID, NO_DELETION, OTHER_SHOP, OWNER_ID, PENDING_DELETION, setWidth, STAFF, transferBody } from "./testing";

const WIDE = 1280;
const PHONE = 375;
const LOGIN = { id: 123456789, first_name: "Ali", auth_date: 1791270000, hash: "ab12" };
const SECOND_CUSTOMER = "11111111-1111-4111-8111-111111111112";

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
  window.sessionStorage.clear();
  setWidth(WIDE);
});
afterEach(cleanup);

/** Stands in for Telegram's widget: a button in its place. Loads nothing and leads nowhere. */
function StubWidget({ botUsername }: LoginWidgetProps) {
  return (
    <button type="button" data-bot={botUsername}>
      telegram
    </button>
  );
}

const RETURNED: LoginReturn = { status: "returned", data: LOGIN };

type Options = {
  role?: Role;
  shops?: number;
  transfer?: unknown;
  deletion?: unknown;
  extra?: (sent: Sent) => Reply | null;
};

/**
 * The server as the panel meets it: sign-in answers a CSRF token, and from then on a request that
 * changes something without that token is refused as unauthenticated, as auth_api.py does.
 */
function backend({ role = "owner", shops = 1, transfer = null, deletion = NO_DELETION, extra = () => null }: Options = {}) {
  const held = { role, transfer, deletion, csrf: CSRF, signIns: 0 };
  const server = fakeServer((sent) => {
    const special = extra(sent);
    if (special !== null) {
      return special;
    }
    if (sent.path === "/api/v1/auth/telegram-login") {
      held.signIns += 1;
      return ok({ csrf_token: held.csrf, expires_at: "2026-10-21T07:00:00+00:00" });
    }
    if (sent.headers["Authorization"] !== undefined) {
      return refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring.");
    }
    if (sent.method !== "GET" && sent.headers["X-CSRF-Token"] !== held.csrf) {
      return refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring.");
    }
    if (sent.method !== "GET") {
      if (sent.path === "/api/v1/me/active-shop") {
        return ok({ active_shop: (sent.body as { shop_id: string }).shop_id });
      }
      if (sent.path.endsWith("/ownership-transfer/accept")) {
        held.role = "owner";
        held.transfer = null;
        return ok(transferBody({ status: "accepted" }));
      }
      if (sent.path === SHOP_BASE) {
        return ok(settingsBody(sent.body as Record<string, unknown>));
      }
      if (sent.path.startsWith(`${SHOP_BASE}/catalog/`)) {
        return ok(itemBody(sent.body as Record<string, unknown>));
      }
      return ok(null);
    }
    const base = sent.path.startsWith(`/api/v1/shops/${OTHER_SHOP}`) ? `/api/v1/shops/${OTHER_SHOP}` : SHOP_BASE;
    switch (sent.path) {
      case "/api/v1/me/shops": {
        const items = [{ shop_id: SHOP_ID, name: "Baraka savdo", role: held.role, membership_id: held.role === "owner" ? OWNER_ID : MANAGER_ID }];
        if (shops > 1) {
          items.push({ shop_id: OTHER_SHOP, name: "Ziyo market", role: "owner", membership_id: OWNER_ID });
        }
        return ok({ items, active_shop: SHOP_ID });
      }
      case "/api/v1/me/owner-totals":
        return ok({
          items: [{ shop_id: SHOP_ID, name: "Baraka savdo", outstanding: 1, debtors: 1, overdue: 0, due_today: 0 }],
          total: { outstanding: 1, debtors: 1, overdue: 0, due_today: 0 },
        });
      case `${base}/overview`:
        return ok({ outstanding: 120000, debtors: 1, overdue: { amount: 45000, customers: 1 }, due_today: 0 });
      case `${base}/overview/debtors`:
        return ok({ items: [{ ...customerBody(), overdue: { amount: 45000, since: "2026-10-01", days: 5, due_today: 0 } }], next_cursor: null });
      case `${base}/customers`:
        return ok({
          items: [customerBody(), customerBody({ id: SECOND_CUSTOMER, display_name: "Vali Aliyev", phone: null, balance: 0 })],
          next_cursor: null,
        });
      case `${base}/customers/${CUSTOMER_ID}`:
        return ok(detailBody({ overdue: NO_OVERDUE }));
      case `${base}/customers/${CUSTOMER_ID}/link`:
        return ok(linkBody());
      case `${base}/credit-settings`:
        return ok(creditSettingsBody());
      case `${base}/catalog`:
        return ok({ items: [itemBody(), itemBody({ id: "44444444-4444-4444-8444-444444444449", name: "Choy", learned: true })], next_cursor: null });
      case base:
        return ok(settingsBody());
      case `${base}/subscription`:
        return ok(subscriptionBody());
      case `${base}/staff`:
        return ok({ items: STAFF });
      case `${base}/staff/invitations`:
        return ok({ items: [] });
      case `${base}/ownership-transfer`:
        return ok({ pending: held.transfer });
      case `${base}/deletion`:
        return ok(held.deletion);
      case `${base}/activity`:
        return ok({ items: [], next_cursor: null });
    }
    return refusal(404, "NOT_FOUND", "Topilmadi.");
  });
  return { ...server, held };
}

let loaded: { server: ReturnType<typeof backend>; botUsername: string | null } | null = null;

function start(server: ReturnType<typeof backend>, botUsername: string | null = "qarz_daftari_bot", loginReturn: LoginReturn = NO_RETURN) {
  loaded = { server, botUsername };
  return render(
    <PanelRoot
      initialLanguage="uz"
      fetch={server.fetch}
      botUsername={botUsername}
      LoginWidget={StubWidget}
      loginReturn={loginReturn}
      now={() => NOON}
    />,
  );
}

const heading = () => screen.getByRole("heading", { level: 1 }).textContent;
/**
 * The person presses the button and confirms in Telegram, and Telegram sends the browser back: the page
 * is loaded again, this time with the signed fields taken from its address.
 */
function signIn(loginReturn: LoginReturn = RETURNED) {
  if (loaded === null) {
    throw new Error("the page was never opened");
  }
  cleanup();
  return start(loaded.server, loaded.botUsername, loginReturn);
}
const mainNav = () => screen.getByRole("navigation", { name: "Asosiy menyu" });
const navLinks = () => within(mainNav()).getAllByRole("link").map((link) => link.textContent);
const paths = (server: ReturnType<typeof backend>) => server.sent.map((sent) => sent.path);

/** Signs in and waits for the overview of the active shop. */
async function open(server: ReturnType<typeof backend>) {
  start(server);
  const view = signIn();
  await screen.findAllByText("Jami qarz");
  return view;
}

describe("the sign-in screen", () => {
  it("is what the panel opens with: the Telegram button, no navigation, and no request", async () => {
    const server = backend();
    start(server);
    expect(heading()).toBe("Panelga kirish");
    expect(screen.getByText("Boshqaruv paneliga Telegram hisobingiz orqali kirasiz. Alohida parol yo'q.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "telegram" }).getAttribute("data-bot")).toBe("qarz_daftari_bot");
    expect(screen.getByText("Xavfsizlik uchun sahifa yangilanganda qayta kirish so'raladi.")).toBeTruthy();
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent).toHaveLength(0);
  });

  it("explains plainly, instead of showing a broken button, when the build names no bot", () => {
    const server = backend();
    start(server, null);
    expect(screen.getByText(/Bu yig'ilmada Telegram boti ko'rsatilmagan/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "telegram" })).toBeNull();
    expect(document.querySelector("script")).toBeNull();
  });

  it("uses Telegram's real widget unless a test replaces it, and only on this screen", async () => {
    const server = backend();
    render(<PanelRoot initialLanguage="uz" fetch={server.fetch} botUsername="qarz_daftari_bot" now={() => NOON} />);
    const script = document.querySelector("script");
    expect(script?.src.startsWith("https://telegram.org/js/telegram-widget.js")).toBe(true);
    expect(script?.getAttribute("data-telegram-login")).toBe("qarz_daftari_bot");
    // Redirect mode: no callback for Telegram's script to build with eval(), and none left on the page.
    expect(script?.getAttribute("data-auth-url")).toBe(window.location.origin + window.location.pathname);
    expect(script?.hasAttribute("data-onauth")).toBe(false);
    cleanup();
    render(<PanelRoot initialLanguage="uz" fetch={server.fetch} botUsername="qarz_daftari_bot" loginReturn={RETURNED} now={() => NOON} />);
    // While the returned fields are with the server, and after, Telegram's script is not on the page.
    expect(document.querySelector("script")).toBeNull();
    await screen.findByText("Jami qarz");
    expect(document.querySelector("script")).toBeNull();
  });

  it("posts the widget's data once, then loads the workspace with the cookie alone", async () => {
    const server = backend();
    await open(server);
    expect(server.sent[0]).toMatchObject({ method: "POST", path: "/api/v1/auth/telegram-login", body: LOGIN });
    expect(server.sent[1]).toMatchObject({ method: "GET", path: "/api/v1/me/shops" });
    expect(heading()).toBe("Umumiy ko'rinish");
    expect(screen.getByRole("banner").querySelector("strong")?.textContent).toBe("Baraka savdo");
    // Reads prove the session by the cookie the browser holds: no token header of either kind.
    for (const sent of server.sent.slice(1)) {
      expect(sent.method).toBe("GET");
      expect(sent.headers["X-CSRF-Token"]).toBeUndefined();
      expect(sent.headers["Authorization"]).toBeUndefined();
    }
    expect(server.held.signIns).toBe(1);
  });

  it("keeps the CSRF token in memory only: not in storage, not in the address, not on the page", async () => {
    const server = backend();
    const view = await open(server);
    go("#/shop-settings");
    await screen.findByLabelText("Do'kon nomi");
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(CSRF);
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(CSRF);
    expect(window.location.href).not.toContain(CSRF);
    expect(document.cookie).toBe("");
    expect(view.container.innerHTML).not.toContain(CSRF);
    expect(server.sent.every((sent) => !JSON.stringify([sent.path, sent.query]).includes(CSRF))).toBe(true);
    // A page that is loaded again has no token, so it starts at sign-in: nothing was kept to resume from.
    view.unmount();
    start(server);
    expect(heading()).toBe("Panelga kirish");
  });

  it("sends nothing when what arrives is not Telegram's data, and says to press again", async () => {
    const server = backend();
    start(server, "qarz_daftari_bot", { status: "refused" });
    expect((await screen.findByRole("alert")).textContent).toBe("Telegram ma'lumotlari qabul qilinmadi. Kirish tugmasini qayta bosing.");
    expect(server.sent).toHaveLength(0);
    expect(heading()).toBe("Panelga kirish");
  });

  it("shows the server's refusal of the signature and lets the person try again", async () => {
    let refuse = true;
    const server = backend({
      extra: (sent) =>
        refuse && sent.path === "/api/v1/auth/telegram-login" ? refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring.") : null,
    });
    start(server);
    signIn();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Avval tizimga kiring.");
    expect(alert.textContent).toContain("Kirish tugmasini qayta bosing.");
    expect(heading()).toBe("Panelga kirish");
    expect(paths(server)).toEqual(["/api/v1/auth/telegram-login"]);
    refuse = false;
    signIn();
    expect(await screen.findByText("Jami qarz")).toBeTruthy();
  });

  it("tells a lost connection apart from a refusal", async () => {
    const server = backend({ extra: (sent) => (sent.path === "/api/v1/auth/telegram-login" ? "offline" : null) });
    start(server);
    signIn();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Serverga ulanib bo'lmadi");
    expect(alert.textContent).not.toContain("qabul qilinmadi");
  });

  it("posts the returned fields once, although React's strict mode runs every effect twice", async () => {
    const server = backend();
    render(
      <StrictMode>
        <PanelRoot initialLanguage="uz" fetch={server.fetch} botUsername="qarz_daftari_bot" LoginWidget={StubWidget} loginReturn={RETURNED} now={() => NOON} />
      </StrictMode>,
    );
    await screen.findAllByText("Jami qarz");
    expect(paths(server).filter((path) => path === "/api/v1/auth/telegram-login")).toHaveLength(1);
  });

  it("does not send the fields again when the sign-in screen comes back after signing out", async () => {
    const server = backend();
    await open(server);
    fireEvent.click(screen.getAllByRole("button", { name: "Chiqish" })[0] as HTMLElement);
    expect(await screen.findByRole("heading", { level: 1, name: "Panelga kirish" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "telegram" })).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(paths(server).filter((path) => path === "/api/v1/auth/telegram-login")).toHaveLength(1);
  });
});

describe("the session", () => {
  it("adds the CSRF token to a write, and the server accepts it", async () => {
    const server = backend();
    await open(server);
    go("#/shop-settings");
    fireEvent.change(await screen.findByLabelText("Odatdagi to'lash muddati, kun"), { target: { value: "14" } });
    fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
    expect(await screen.findByText("Sozlamalar saqlandi.")).toBeTruthy();
    const write = server.writes().at(-1);
    expect(write).toMatchObject({ method: "PATCH", path: SHOP_BASE, body: { default_promise_days: 14 } });
    expect(write?.headers["X-CSRF-Token"]).toBe(CSRF);
    expect(write?.headers["Authorization"]).toBeUndefined();
  });

  it("returns to sign-in when any call is answered 401, and says the session ended", async () => {
    let expired = false;
    const server = backend({ extra: (sent) => (expired && !sent.path.includes("/auth/") ? refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring.") : null) });
    await open(server);
    expired = true;
    go("#/customers");
    expect(await screen.findByRole("heading", { level: 1, name: "Panelga kirish" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Sessiya tugadi. Davom etish uchun qayta kiring.");
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(screen.queryByText("Ali Valiyev")).toBeNull();
    expect(screen.getByRole("button", { name: "telegram" })).toBeTruthy();

    // Signing in again resumes at the same address with the new token.
    expired = false;
    server.held.csrf = "csrf-second-session-000000000000000000000";
    signIn();
    expect(await screen.findByRole("table", { name: "Mijozlar ro'yxati" })).toBeTruthy();
    expect(heading()).toBe("Mijozlar");
  });

  it("returns to sign-in when a write is refused as unauthenticated", async () => {
    const server = backend();
    await open(server);
    go("#/shop-settings");
    fireEvent.change(await screen.findByLabelText("Odatdagi to'lash muddati, kun"), { target: { value: "14" } });
    server.held.csrf = "rotated-on-the-server-0000000000000000000";
    fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Panelga kirish" })).toBeTruthy();
  });

  it("signs out from the side navigation: tells the server, with the token, and shows sign-in", async () => {
    const server = backend();
    await open(server);
    const before = server.sent.length;
    fireEvent.click(within(mainNav()).getByRole("button", { name: "Chiqish" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Panelga kirish" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Paneldan chiqdingiz.");
    const sent = server.sent[before];
    expect(sent).toMatchObject({ method: "POST", path: "/api/v1/auth/sign-out" });
    expect(sent?.headers["X-CSRF-Token"]).toBe(CSRF);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent).toHaveLength(before + 1);
  });

  it("stays signed in, and says why, when signing out fails", async () => {
    const server = backend({ extra: (sent) => (sent.path === "/api/v1/auth/sign-out" ? "offline" : null) });
    await open(server);
    fireEvent.click(within(mainNav()).getByRole("button", { name: "Chiqish" }));
    expect((await within(mainNav()).findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    expect(heading()).toBe("Umumiy ko'rinish");
    expect((within(mainNav()).getByRole("button", { name: "Chiqish" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("treats a session the server no longer knows as signed out", async () => {
    const server = backend({ extra: (sent) => (sent.path === "/api/v1/auth/sign-out" ? refusal(401, "UNAUTHENTICATED", "Avval tizimga kiring.") : null) });
    await open(server);
    fireEvent.click(within(mainNav()).getByRole("button", { name: "Chiqish" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Panelga kirish" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Paneldan chiqdingiz.");
  });

  it("signs out everywhere only after a confirmation: tells the server, with the token, and shows sign-in saying so", async () => {
    const server = backend();
    await open(server);
    const before = server.sent.length;
    fireEvent.click(screen.getByRole("button", { name: "Barcha qurilmalarda chiqish" }));
    expect(screen.getByText(/Hisobingiz barcha qurilmalarda yopiladi/)).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent).toHaveLength(before);
    fireEvent.click(screen.getByRole("button", { name: "Ha, chiqish" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Panelga kirish" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Barcha qurilmalarda hisobingizdan chiqdingiz.");
    const sent = server.sent[before];
    expect(sent).toMatchObject({ method: "POST", path: "/api/v1/auth/sign-out-everywhere" });
    expect(sent?.headers["X-CSRF-Token"]).toBe(CSRF);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent).toHaveLength(before + 1);
  });

  it("stays in the panel when the person answers no to signing out everywhere", async () => {
    const server = backend();
    await open(server);
    const before = server.sent.length;
    fireEvent.click(screen.getByRole("button", { name: "Barcha qurilmalarda chiqish" }));
    fireEvent.click(screen.getByRole("button", { name: "Bekor qilish" }));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(server.sent).toHaveLength(before);
    expect(heading()).toBe("Umumiy ko'rinish");
    expect(screen.getByRole("button", { name: "Barcha qurilmalarda chiqish" })).toBeTruthy();
  });

  it("stays signed in, and says why, when signing out everywhere fails", async () => {
    const server = backend({ extra: (sent) => (sent.path === "/api/v1/auth/sign-out-everywhere" ? "offline" : null) });
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Barcha qurilmalarda chiqish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, chiqish" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Serverga ulanib bo'lmadi"));
    expect(heading()).toBe("Umumiy ko'rinish");
  });
});

describe("desktop layout, from 1024 px", () => {
  it("keeps the navigation as a landmark with every section of the role in it", async () => {
    const server = backend();
    await open(server);
    expect(mainNav().tagName).toBe("NAV");
    expect(navLinks()).toContain("Xodimlar");
    expect(navLinks()).toContain("Amallar jurnali");
    expect(within(mainNav()).getAllByRole("link").filter((link) => link.getAttribute("aria-current") === "page").map((link) => link.textContent)).toEqual([
      "Umumiy ko'rinish",
    ]);
  });

  it("shows debtors as a table with a header cell per row and a link to each customer", async () => {
    const server = backend();
    await open(server);
    const table = await screen.findByRole("table", { name: "Qarzdorlar" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
      "Mijoz",
      "Qarz",
      "Muddati o'tgan",
      "Kechikish",
      "Bugun to'lanadi",
    ]);
    const name = within(table).getByRole("rowheader", { name: "Ali Valiyev" });
    expect(within(name).getByRole("link").getAttribute("href")).toBe(`#/customers/${CUSTOMER_ID}`);
    expect(within(table).getByText("5 kun kechikkan")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "" })?.classList.contains("rows") ?? false).toBe(false);
  });

  it("shows customers as a table in the order the server returned them: the API has no sort", async () => {
    const server = backend();
    await open(server);
    go("#/customers");
    const table = await screen.findByRole("table", { name: "Mijozlar ro'yxati" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Mijoz", "Telefon", "Qarz"]);
    expect(within(table).getAllByRole("rowheader").map((cell) => cell.textContent)).toEqual(["Ali Valiyev", "Vali Aliyev"]);
    expect(within(table).queryAllByRole("button")).toHaveLength(0);
    expect(server.sent.find((sent) => sent.path === `${SHOP_BASE}/customers`)?.query).toEqual({ status: "active" });
    expect(document.querySelector("ul.rows")).toBeNull();
  });

  it("offers the two entries in the table when a customer is picked for a new entry", async () => {
    const server = backend();
    await open(server);
    go("#/new");
    const table = await screen.findByRole("table", { name: "Mijozlar ro'yxati" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(within(rows[0] as HTMLElement).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      `#/customers/${CUSTOMER_ID}/credit`,
      `#/customers/${CUSTOMER_ID}/payment`,
    ]);
    // Nothing is owed, so there is nothing to pay.
    expect(within(rows[1] as HTMLElement).getAllByRole("link").map((link) => link.textContent)).toEqual(["Nasiya"]);
  });

  it("opens a customer beside the customer book, and marks them in the list", async () => {
    const server = backend();
    await open(server);
    go(`#/customers/${CUSTOMER_ID}`);
    expect(await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" })).toBeTruthy();
    const book = screen.getByRole("region", { name: "Mijozlar" });
    const current = (await within(book).findAllByRole("link")).filter((link) => link.getAttribute("aria-current") === "page");
    expect(current.map((link) => link.getAttribute("href"))).toEqual([`#/customers/${CUSTOMER_ID}`]);
    // Beside the customer the book is a narrow column of rows, not a table.
    expect(within(book).queryByRole("table")).toBeNull();
    expect(within(book).getByRole("link", { name: /Vali Aliyev/ }).getAttribute("href")).toBe(`#/customers/${SECOND_CUSTOMER}`);
    expect(heading()).toBe("Mijoz");
  });

  it("shows the catalog as a table, with the controls of a role that may change it", async () => {
    const server = backend();
    await open(server);
    go("#/catalog");
    const table = await screen.findByRole("table", { name: "Katalogdagi mahsulotlar" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
      "Mahsulot",
      "Birlik",
      "Narxi",
      "Belgi",
      "Amallar",
    ]);
    const learned = within(table).getByRole("rowheader", { name: "Choy" }).closest("tr") as HTMLElement;
    expect(within(learned).getByText("Yangi: hali ko'rib chiqilmagan")).toBeTruthy();
    expect(within(learned).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Tahrirlash",
      "Yashirish",
      "Qabul qilish",
      "Rad etish",
      "Birlashtirish",
    ]);

    // The form of an item opens in a row of its own under it, and saves as on a phone.
    const bread = within(table).getByRole("rowheader", { name: "Non" }).closest("tr") as HTMLElement;
    fireEvent.click(within(bread).getByRole("button", { name: "Tahrirlash" }));
    const form = within(table).getByRole("form", { name: "Tahrirlash" });
    expect(form.closest("td")?.getAttribute("colspan")).toBe("5");
    expect(within(bread).queryByRole("button")).toBeNull();
    fireEvent.change(within(form).getByLabelText("Narxi, so'm"), { target: { value: "4500" } });
    fireEvent.click(within(form).getByRole("button", { name: "Saqlash" }));
    // Two writes so far: the sign-in, and this change.
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[1]).toMatchObject({ method: "PATCH", body: { price: 4500 } });
    expect(server.writes()[1]?.headers["X-CSRF-Token"]).toBe(CSRF);
  });

  it("has the shop switcher in the side navigation for someone with several shops", async () => {
    const server = backend({ shops: 2 });
    await open(server);
    const select = within(mainNav()).getByLabelText("Do'konni almashtirish") as HTMLSelectElement;
    expect([...select.options].map((option) => option.textContent)).toEqual(["Baraka savdo — Do'kon egasi", "Ziyo market — Do'kon egasi"]);
    expect(select.value).toBe(SHOP_ID);
    expect(screen.queryByRole("button", { name: "Boshqa do'konga o'tish" })).toBeNull();

    fireEvent.change(select, { target: { value: OTHER_SHOP } });
    await waitFor(() => expect(screen.getByRole("banner").querySelector("strong")?.textContent).toBe("Ziyo market"));
    const write = server.writes().at(-1);
    expect(write).toMatchObject({ method: "PUT", path: "/api/v1/me/active-shop", body: { shop_id: OTHER_SHOP } });
    expect(write?.headers["X-CSRF-Token"]).toBe(CSRF);
    await waitFor(() => expect(paths(server)).toContain(`/api/v1/shops/${OTHER_SHOP}/overview`));
  });

  it("shows the refusal when the shop cannot be made active, and stays in the shop", async () => {
    const server = backend({ shops: 2, extra: (sent) => (sent.method === "PUT" ? refusal(404, "NOT_FOUND", "Topilmadi.") : null) });
    await open(server);
    fireEvent.change(within(mainNav()).getByLabelText("Do'konni almashtirish"), { target: { value: OTHER_SHOP } });
    expect((await within(mainNav()).findByRole("alert")).textContent).toContain("Topilmadi.");
    expect(screen.getByRole("banner").querySelector("strong")?.textContent).toBe("Baraka savdo");
  });

  it("has no switcher for someone with one shop", async () => {
    const server = backend();
    await open(server);
    expect(within(mainNav()).queryByLabelText("Do'konni almashtirish")).toBeNull();
    expect(within(mainNav()).getByRole("button", { name: "Chiqish" })).toBeTruthy();
  });
});

describe("under 1024 px the panel is the phone layout", () => {
  beforeEach(() => setWidth(PHONE));

  it("draws the lists as rows, not tables, and one customer without the book beside it", async () => {
    const server = backend();
    await open(server);
    expect(screen.queryByRole("table")).toBeNull();
    expect(document.querySelectorAll("ul.rows li.row")).toHaveLength(1);
    go("#/customers");
    await screen.findByText("Vali Aliyev");
    expect(screen.queryByRole("table")).toBeNull();
    expect(document.querySelectorAll("ul.rows li.row")).toHaveLength(2);
    go("#/catalog");
    await screen.findByText("Choy");
    expect(screen.queryByRole("table")).toBeNull();

    const before = server.sent.filter((sent) => sent.path === `${SHOP_BASE}/customers`).length;
    go(`#/customers/${CUSTOMER_ID}`);
    await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
    expect(screen.queryByRole("region", { name: "Mijozlar" })).toBeNull();
    expect(server.sent.filter((sent) => sent.path === `${SHOP_BASE}/customers`)).toHaveLength(before);
  });

  it("puts the shop switch and sign-out with the overview's buttons, where the tab bar leaves room", async () => {
    const server = backend({ shops: 2 });
    await open(server);
    expect(within(mainNav()).queryByRole("button")).toBeNull();
    expect(within(mainNav()).queryByRole("combobox")).toBeNull();
    expect(screen.getByRole("button", { name: "Boshqa do'konga o'tish" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Chiqish" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Panelga kirish" })).toBeTruthy();
    expect(server.sent.at(-1)).toMatchObject({ method: "POST", path: "/api/v1/auth/sign-out" });
  });

  it("still has the back office, as tables that the style sheet stacks", async () => {
    const server = backend();
    await open(server);
    go("#/staff");
    expect(await screen.findByRole("table", { name: "Xodimlar ro'yxati" })).toBeTruthy();
  });
});

describe("what a role is offered", () => {
  it("gives the owner the staff, the activity log, shop deletion and the totals of their shops", async () => {
    const server = backend({ shops: 2 });
    await open(server);
    expect(await screen.findByRole("table", { name: "Barcha do'konlarim" })).toBeTruthy();
    go("#/staff");
    expect(heading()).toBe("Xodimlar");
    expect(await screen.findByRole("table", { name: "Xodimlar ro'yxati" })).toBeTruthy();
    go("#/activity");
    expect(heading()).toBe("Amallar jurnali");
    expect(await screen.findByLabelText("Amal turi")).toBeTruthy();
    go("#/shop-settings");
    expect(await screen.findByRole("heading", { level: 2, name: "Do'konni o'chirish" })).toBeTruthy();
    expect(screen.queryByText("Bu bo'lim tez orada tayyor bo'ladi.")).toBeNull();
  });

  it("does not ask for the totals of an owner of one shop", async () => {
    const server = backend();
    await open(server);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByText("Barcha do'konlarim")).toBeNull();
    expect(paths(server)).not.toContain("/api/v1/me/owner-totals");
  });

  it("tells the owner on every screen while the shop waits to be deleted", async () => {
    const server = backend({ deletion: PENDING_DELETION });
    await open(server);
    for (const hash of ["#/", "#/customers", "#/catalog", "#/staff"]) {
      go(hash);
      const notice = await screen.findByText("Do'konni o'chirish so'ralgan. Do'kon 2026-yil 5-noyabr da butunlay o'chiriladi.");
      expect(within(notice.parentElement as HTMLElement).getByRole("link").getAttribute("href"), hash).toBe("#/shop-settings");
    }
    // One read serves every screen of the shop.
    expect(paths(server).filter((path) => path === `${SHOP_BASE}/deletion`)).toHaveLength(1);
  });

  it("offers a manager no staff, no activity log, no ownership offer to make and no deletion", async () => {
    const server = backend({ role: "manager" });
    await open(server);
    expect(navLinks()).not.toContain("Xodimlar");
    expect(navLinks()).not.toContain("Amallar jurnali");
    for (const hash of ["#/staff", "#/activity"]) {
      go(hash);
      expect(heading()).toBe("Sahifa topilmadi");
    }
    go("#/shop-settings");
    await screen.findByText("Sozlamalarni faqat do'kon egasi o'zgartira oladi.");
    expect(screen.queryByText("Do'konni o'chirish")).toBeNull();
    expect(screen.queryByRole("button", { name: "Do'konni o'chirishni so'rash" })).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 20));
    const asked = paths(server);
    expect(asked.filter((path) => /\/(staff|activity|deletion)/.test(path))).toEqual([]);
    expect(asked).not.toContain("/api/v1/me/owner-totals");
    expect(server.writes().map((sent) => sent.path)).toEqual(["/api/v1/auth/telegram-login"]);
  });

  it("shows a manager an ownership offer addressed to them; accepting makes them the owner", async () => {
    const server = backend({ role: "manager", transfer: transferBody({ to_membership: MANAGER_ID }) });
    await open(server);
    const offer = await screen.findByRole("region", { name: "Egalikni o'tkazish" });
    expect(navLinks()).not.toContain("Xodimlar");
    fireEvent.click(within(offer).getByRole("button", { name: "Qabul qilish" }));
    fireEvent.click(within(offer).getByRole("button", { name: "Ha, qabul qilaman" }));
    // Their shops and roles are read again: the navigation is now the owner's.
    await waitFor(() => expect(navLinks()).toContain("Xodimlar"));
    expect(screen.queryByRole("region", { name: "Egalikni o'tkazish" })).toBeNull();
    expect(paths(server).filter((path) => path === "/api/v1/me/shops")).toHaveLength(2);
    expect(server.held.signIns).toBe(1);
  });

  it("offers a seller none of it and asks the server for none of it", async () => {
    const server = backend({ role: "seller", transfer: transferBody({ to_membership: MANAGER_ID }), deletion: PENDING_DELETION });
    await open(server);
    expect(navLinks()).toEqual(["Umumiy ko'rinish", "Mijozlar", "Yangi yozuv", "Katalog"]);
    go("#/catalog");
    const table = await screen.findByRole("table", { name: "Katalogdagi mahsulotlar" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Mahsulot", "Birlik", "Narxi", "Belgi"]);
    expect(within(table).queryAllByRole("button")).toHaveLength(0);
    go("#/staff");
    expect(heading()).toBe("Sahifa topilmadi");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(paths(server).filter((path) => /staff|activity|deletion|ownership|owner-totals/.test(path))).toEqual([]);
    expect(screen.queryByRole("region", { name: "Egalikni o'tkazish" })).toBeNull();
    expect(screen.queryByText(/butunlay o'chiriladi/)).toBeNull();
  });
});
