// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { uzAdmin } from "../i18n/admin/uz";
import { I18nProvider } from "../i18n/I18nProvider";
import { CUSTOMER_ID, customerBody, deferred, detailBody, entryBody, type Reply, type Sent } from "../testing/fakeServer";
import { go } from "../testing/renderScreen";
import { AdminRoutes } from "./AdminApp";
import { AuditScreen } from "./AuditScreen";
import { isOpenAccess, parseHours, SUPPORT_HOURS_MAX, SUPPORT_HOURS_MIN } from "./rules";
import { ShopScreen } from "./ShopScreen";
import { CustomerScreen } from "./support/CustomerScreen";
import { supportCustomers } from "./support/customers";
import { CustomersScreen } from "./support/CustomersScreen";
import { SupportAccessScreen } from "./SupportAccessScreen";
import { SupportSection, type Who } from "./SupportSection";
import {
  ACCESS_ID,
  ADMIN,
  ADMIN_ID,
  adminApi,
  auditBody,
  cells,
  CSRF,
  KNOWN,
  NOBODY,
  NOW,
  ok,
  OTHER_ADMIN,
  OTHER_SHOP,
  refusal,
  renderAdmin,
  SHOP_ID,
  shopDetailBody,
  supportBody,
} from "./testing";

beforeEach(() => {
  window.location.hash = "";
});
afterEach(cleanup);

const now = () => NOW;
const SHOP = { id: SHOP_ID, name: "Baraka savdo" };
const LIST = `${ADMIN}/support-access`;
const OPEN = `${ADMIN}/shops/${SHOP_ID}/support-access`;
const CLOSE = `${OPEN}/close`;
const CUSTOMERS = `${ADMIN}/shops/${SHOP_ID}/customers`;
const others = supportBody({ id: "88888888-8888-4888-8888-888888888882", admin_id: OTHER_ADMIN, reason: "Hisobot xatosi" });
const REQUIRED = refusal(403, "SUPPORT_ACCESS_REQUIRED", "Yordam uchun kirish ochilmagan.");
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe("the administrator's support-access API", () => {
  const KEY = "key-0000-0001";

  it("opens with the reason and the hours, closes with a key alone, and lists with its filters, all under /api/admin/v1", async () => {
    const { server, api } = adminApi((sent) =>
      sent.path === LIST
        ? ok({ items: [supportBody()], next_cursor: "c2" })
        : sent.path === `${ADMIN}/audit`
          ? ok({ items: [], next_cursor: null })
          : sent.path === CLOSE
            ? ok({ id: ACCESS_ID, shop_id: SHOP_ID, state: "closed" })
            : ok(supportBody(), 201),
    );
    expect(await api.openSupport(SHOP_ID, { reason: "Egasi yordam so'radi", hours: 3 }, KEY)).toEqual({
      id: ACCESS_ID,
      shopId: SHOP_ID,
      shopName: "Baraka savdo",
      adminId: ADMIN_ID,
      reason: "Egasi yordam so'radi",
      state: "active",
      startsAt: "2026-10-06T06:00:00+00:00",
      endsAt: "2026-10-06T08:00:00+00:00",
      closedAt: null,
      closedBy: null,
    });
    expect(await api.closeSupport(SHOP_ID, KEY)).toBeUndefined();
    expect((await api.listSupport({ shopId: SHOP_ID, open: true, cursor: "c1" })).nextCursor).toBe("c2");
    await api.listSupport({});
    await api.listSupport({ open: false });
    await api.listAudit({ adminId: ADMIN_ID, action: "support" });
    expect(server.sent.map((sent) => [sent.method, sent.path.slice(ADMIN.length), sent.query, sent.body, sent.headers["Idempotency-Key"]])).toEqual([
      ["POST", `/shops/${SHOP_ID}/support-access`, {}, { reason: "Egasi yordam so'radi", hours: 3 }, KEY],
      ["POST", `/shops/${SHOP_ID}/support-access/close`, {}, undefined, KEY],
      ["GET", "/support-access", { shop_id: SHOP_ID, open: "true", cursor: "c1" }, undefined, undefined],
      ["GET", "/support-access", {}, undefined, undefined],
      ["GET", "/support-access", {}, undefined, undefined],
      ["GET", "/audit", { admin_id: ADMIN_ID, action: "support" }, undefined, undefined],
    ]);
    expect(server.sent.every((sent) => sent.path.startsWith(`${ADMIN}/`))).toBe(true);
    expect(server.writes().every((sent) => sent.headers["X-CSRF-Token"] === CSRF)).toBe(true);
  });

  it("reads an access the server names no shop for, and refuses answers that are not the contract", async () => {
    const { api } = adminApi(() => ok(supportBody({ shop_name: undefined }), 201));
    expect((await api.openSupport(SHOP_ID, { reason: "Sabab", hours: 1 }, KEY)).shopName).toBeNull();
    for (const wrong of [{ admin_id: null }, { ends_at: 5 }, { reason: undefined }]) {
      await expect(adminApi(() => ok({ items: [supportBody(wrong)], next_cursor: null })).api.listSupport({})).rejects.toMatchObject({ code: "BAD_RESPONSE" });
    }
  });

  it("reads a shop's customers and one customer's page under the admin API, and writes nothing", async () => {
    const { server, api } = adminApi((sent) => (sent.path === CUSTOMERS ? ok({ items: [customerBody()], next_cursor: null }) : ok(detailBody())));
    const client = supportCustomers(api);
    expect((await client.list(SHOP_ID, { q: "ali", status: "archived", cursor: "c1" })).items[0]).toMatchObject({ displayName: "Ali Valiyev", balance: 120000 });
    expect(await client.read(SHOP_ID, CUSTOMER_ID)).toMatchObject({ displayName: "Ali Valiyev", entriesTotal: 1 });
    expect(server.sent.map((sent) => [sent.method, sent.path, sent.query])).toEqual([
      ["GET", CUSTOMERS, { q: "ali", status: "archived", cursor: "c1" }],
      ["GET", `${CUSTOMERS}/${CUSTOMER_ID}`, {}],
    ]);
    expect(Object.keys(client).sort()).toEqual(["list", "read"]);
  });

  it("carries the refusal of someone with no open access", async () => {
    const { api } = adminApi(() => REQUIRED);
    await expect(supportCustomers(api).list(SHOP_ID, {})).rejects.toMatchObject({ code: "SUPPORT_ACCESS_REQUIRED", status: 403 });
  });

  it("takes whole hours from 1 to 24 and nothing else", () => {
    expect([SUPPORT_HOURS_MIN, SUPPORT_HOURS_MAX]).toEqual([1, 24]);
    expect(["1", "24", " 8 ", "08"].map(parseHours)).toEqual([1, 24, 8, 8]);
    expect(["0", "25", "1.5", "", "-1", "bir", "100", "1e1", "２"].map(parseHours)).toEqual([null, null, null, null, null, null, null, null, null]);
  });

  it("calls an access open only while the server says active and its time has not run out", () => {
    const access = (state: string, endsAt = "2026-10-06T08:00:00+00:00") => ({ state, endsAt });
    expect(isOpenAccess(access("active"), NOW)).toBe(true);
    expect(isOpenAccess(access("active"), new Date("2026-10-06T08:00:00Z"))).toBe(false);
    expect(["expired", "closed", ""].map((state) => isOpenAccess(access(state), NOW))).toEqual([false, false, false]);
    expect(isOpenAccess(access("active", "later"), NOW)).toBe(false);
  });
});

describe("support access on a shop's page", () => {
  const section = (open: unknown[] = [], who: Who = NOBODY, onWrite: (sent: Sent) => Reply = (sent) => (sent.path === CLOSE ? ok({ id: ACCESS_ID, shop_id: SHOP_ID, state: "closed" }) : ok(supportBody(), 201))) => {
    const held = { open };
    const made = adminApi((sent) => (sent.method === "GET" ? ok({ items: held.open, next_cursor: null }) : onWrite(sent)));
    renderAdmin(<SupportSection api={made.api} shop={SHOP} now={now} who={who} />);
    return { server: made.server, held };
  };
  const offered = () => within(screen.getByRole("region", { name: "Yordam uchun kirish" })).queryAllByRole("button").map((button) => button.textContent);
  const fill = (reason: string, hours: string) => {
    fireEvent.change(screen.getByLabelText("Sabab"), { target: { value: reason } });
    fireEvent.change(screen.getByLabelText("Davomiyligi, soat"), { target: { value: hours } });
    fireEvent.click(screen.getByRole("button", { name: "Davom etish" }));
  };

  it("asks for the shop's open accesses alone, says when there is none, and offers only to open one", async () => {
    const { server } = section();
    expect(await screen.findByText("Bu do'konga hozir ochiq kirish yo'q.")).toBeTruthy();
    expect(server.sent.map((sent) => [sent.method, sent.path, sent.query])).toEqual([["GET", LIST, { shop_id: SHOP_ID, open: "true" }]]);
    expect(offered()).toEqual(["Kirish ochish"]);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText(/har bir ko'rishingiz yozib boriladi va egasiga ko'rsatiladi/)).toBeTruthy();
  });

  it.each([
    ["", "1", "Sabab 3 dan 500 belgigacha bo'lishi kerak.", "support-reason-error"],
    ["ab", "1", "Sabab 3 dan 500 belgigacha bo'lishi kerak.", "support-reason-error"],
    ["Egasi so'radi", "0", "Soat 1 dan 24 gacha butun son bo'lishi kerak.", "support-hours-error"],
    ["Egasi so'radi", "25", "Soat 1 dan 24 gacha butun son bo'lishi kerak.", "support-hours-error"],
    ["Egasi so'radi", "1.5", "Soat 1 dan 24 gacha butun son bo'lishi kerak.", "support-hours-error"],
    ["Egasi so'radi", "", "Soat 1 dan 24 gacha butun son bo'lishi kerak.", "support-hours-error"],
  ])("goes no further with the reason %j and %j hours", async (reason, hours, message, id) => {
    const { server } = section();
    fireEvent.click(await screen.findByRole("button", { name: "Kirish ochish" }));
    fill(reason, hours);
    expect(screen.getByText(message).id).toBe(id);
    expect(screen.queryByRole("button", { name: "Ha, ochilsin" })).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });

  it("asks before opening, sends nothing on going back, and one request with a key, the reason and the hours on yes", async () => {
    const learn = vi.fn();
    const held = deferred<ReturnType<typeof ok>>();
    const { server, held: shop } = section([], { me: null, learn }, () => held.promise);
    fireEvent.click(await screen.findByRole("button", { name: "Kirish ochish" }));
    expect((screen.getByLabelText("Davomiyligi, soat") as HTMLInputElement).value).toBe("1");
    fill("  Egasi   yordam so'radi ", " 2 ");
    expect(screen.getByText("Baraka savdo: 2 soatga kirish ochilsinmi?")).toBeTruthy();
    expect(screen.getByText("Sabab: Egasi yordam so'radi")).toBeTruthy();
    expect(screen.getByText("Do'kon egasiga sabab va tugash vaqti xabar qilinadi. Har bir ko'rishingiz yozib boriladi va egasiga ko'rsatiladi.")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Orqaga" }));
    expect(server.writes()).toHaveLength(0);
    expect((screen.getByLabelText("Sabab") as HTMLTextAreaElement).value).toBe("  Egasi   yordam so'radi ");

    fireEvent.click(screen.getByRole("button", { name: "Davom etish" }));
    const yes = screen.getByRole("button", { name: "Ha, ochilsin" }) as HTMLButtonElement;
    fireEvent.click(yes);
    fireEvent.click(yes);
    await waitFor(() => expect(yes.disabled).toBe(true));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: OPEN });
    expect(server.writes()[0]?.body).toEqual({ reason: "Egasi yordam so'radi", hours: 2 });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(server.writes()[0]?.headers["X-CSRF-Token"]).toBe(CSRF);
    expect(learn).not.toHaveBeenCalled();
    shop.open = [supportBody()];
    held.resolve(ok(supportBody(), 201));
    expect(await screen.findByText("Kirish ochildi va 2026-yil 6-oktabr, 13:00 da tugaydi.")).toBeTruthy();
    // The answer says which administrator this is: the panel remembers it.
    expect(learn).toHaveBeenCalledWith(ADMIN_ID);
    expect(server.sent.filter((sent) => sent.method === "GET")).toHaveLength(2);
  });

  it("beside the caller's own access offers the customers and the closing, and no second opening", async () => {
    section([supportBody(), others], KNOWN);
    const list = await screen.findByRole("list", { name: "Hozir ochiq kirishlar" });
    expect(within(list).getAllByRole("listitem").map((item) => [...item.querySelectorAll("p")].map((part) => part.textContent))).toEqual([
      ["Sizning kirishingiz · 2026-yil 6-oktabr, 13:00 gacha ochiq", "Sabab: Egasi yordam so'radi"],
      ["Administrator d4e5f6 · 2026-yil 6-oktabr, 13:00 gacha ochiq", "Sabab: Hisobot xatosi"],
    ]);
    expect(screen.getByRole("link", { name: "Mijozlarni ko'rish (faqat o'qish)" }).getAttribute("href")).toBe(`#/shops/${SHOP_ID}/customers`);
    expect(offered()).toEqual(["Kirishimni yopish"]);
    expect(screen.queryByText(/Server qaysi kirish sizniki ekanini aytmaydi/)).toBeNull();
  });

  it("offers neither the customers nor the closing beside a colleague's access, only an opening of one's own", async () => {
    section([others], KNOWN);
    expect(await screen.findByText("Administrator d4e5f6 · 2026-yil 6-oktabr, 13:00 gacha ochiq")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    expect(offered()).toEqual(["Kirish ochish"]);
  });

  it("offers everything, and says why, while it does not know whose the open access is", async () => {
    section([supportBody()]);
    expect(await screen.findByText("Administrator a1b2c3 · 2026-yil 6-oktabr, 13:00 gacha ochiq")).toBeTruthy();
    expect(screen.getByText(/Server qaysi kirish sizniki ekanini aytmaydi/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Mijozlarni ko'rish (faqat o'qish)" })).toBeTruthy();
    expect(offered()).toEqual(["Kirishimni yopish", "Kirish ochish"]);
  });

  it("does not count an access whose time has run out since the server answered", async () => {
    section([supportBody({ ends_at: "2026-10-06T06:59:59+00:00" })], KNOWN);
    expect(await screen.findByText("Bu do'konga hozir ochiq kirish yo'q.")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    expect(offered()).toEqual(["Kirish ochish"]);
  });

  it("asks before closing, sends nothing on going back, and one request with a key and no body on yes", async () => {
    const { server, held } = section([supportBody()], KNOWN);
    fireEvent.click(await screen.findByRole("button", { name: "Kirishimni yopish" }));
    expect(screen.getByText("Baraka savdo: bu do'konga kirishingiz hozir yopilsinmi?")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Orqaga" }));
    expect(server.writes()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Kirishimni yopish" }));
    held.open = [];
    fireEvent.click(screen.getByRole("button", { name: "Ha, yopilsin" }));
    expect(await screen.findByText("Kirish yopildi.")).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: CLOSE });
    expect(server.writes()[0]?.body).toBeUndefined();
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(await screen.findByText("Bu do'konga hozir ochiq kirish yo'q.")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("says the server's words when nothing of the caller's was open to close, and reads the accesses again", async () => {
    const { server } = section([supportBody()], NOBODY, () => refusal(409, "SUPPORT_ACCESS_NOT_OPEN", "Sizning ochiq kirishingiz yo'q."));
    fireEvent.click(await screen.findByRole("button", { name: "Kirishimni yopish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, yopilsin" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Sizning ochiq kirishingiz yo'q.");
    await waitFor(() => expect(server.sent.filter((sent) => sent.method === "GET")).toHaveLength(2));
    expect(screen.queryByRole("button", { name: "Ha, yopilsin" })).toBeNull();
    expect(screen.queryByText("Kirish yopildi.")).toBeNull();

    // "Try again" reads the accesses once more and puts the refusal away; it writes nothing.
    fireEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "Qayta urinish" }));
    await waitFor(() => expect(server.sent.filter((sent) => sent.method === "GET")).toHaveLength(3));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(server.writes()).toHaveLength(1);
  });

  it("says the server's words when the caller's own access is open already, and reads the accesses again", async () => {
    const { server } = section([others], NOBODY, () => refusal(409, "SUPPORT_ACCESS_ALREADY_OPEN", "Bu do'konga kirishingiz allaqachon ochiq."));
    fireEvent.click(await screen.findByRole("button", { name: "Kirish ochish" }));
    fill("Egasi so'radi", "1");
    fireEvent.click(screen.getByRole("button", { name: "Ha, ochilsin" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Bu do'konga kirishingiz allaqachon ochiq.");
    await waitFor(() => expect(server.sent.filter((sent) => sent.method === "GET")).toHaveLength(2));
    expect(screen.queryByRole("button", { name: "Ha, ochilsin" })).toBeNull();
  });

  it.each([
    ["hours", "Soat 1 dan 24 gacha butun son bo'lishi kerak."],
    ["reason", "Sabab 3 dan 500 belgigacha bo'lishi kerak."],
  ])("explains a %s the server refused, and a retry resends the same key", async (field, message) => {
    const { server } = section([], NOBODY, () => refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { [field]: "must be between" }));
    fireEvent.click(await screen.findByRole("button", { name: "Kirish ochish" }));
    fill("Egasi so'radi", "4");
    fireEvent.click(screen.getByRole("button", { name: "Ha, ochilsin" }));
    expect(await screen.findByText(message)).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toBe("Ma'lumotlar noto'g'ri kiritilgan.");
    fireEvent.click(screen.getByRole("button", { name: "Ha, ochilsin" }));
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[1]?.headers["Idempotency-Key"]).toBe(server.writes()[0]?.headers["Idempotency-Key"]);
  });

  it("is part of the shop's page, which still names no customer whatever the server sends", async () => {
    const leaked = shopDetailBody({ customers: [{ display_name: "Ali Valiyev", balance: 120000 }] });
    const made = adminApi((sent) => (sent.path === LIST ? ok({ items: [{ ...supportBody(), customers: [{ display_name: "Ali Valiyev" }] }], next_cursor: null }) : ok(leaked)));
    renderAdmin(<ShopScreen api={made.api} shopId={SHOP_ID} now={now} who={KNOWN} />);
    expect(await screen.findByText("Sizning kirishingiz · 2026-yil 6-oktabr, 13:00 gacha ochiq")).toBeTruthy();
    expect(document.body.textContent).not.toContain("Ali Valiyev");
    expect(made.server.sent.map((sent) => sent.path).sort()).toEqual([`${ADMIN}/shops/${SHOP_ID}`, LIST].sort());
  });
});

describe("the list of support accesses", () => {
  const ROWS = [
    supportBody(),
    supportBody({ id: "s2", shop_id: OTHER_SHOP, shop_name: "Ziyo market", admin_id: OTHER_ADMIN, state: "closed", closed_at: "2026-10-05T06:30:00+00:00", closed_by: "owner" }),
    supportBody({ id: "s3", state: "closed", closed_at: "2026-10-05T06:30:00+00:00", closed_by: "admin" }),
    supportBody({ id: "s4", state: "expired", shop_name: undefined }),
    supportBody({ id: "s5", state: "closed", closed_at: "2026-10-05T06:30:00+00:00", closed_by: "robot" }),
  ];
  const open = (shopId: string | null = null, who: Who = KNOWN, handler: (sent: Sent) => Reply = () => ok({ items: ROWS, next_cursor: null })) => {
    const made = adminApi(handler);
    renderAdmin(<SupportAccessScreen api={made.api} shopId={shopId} now={now} who={who} />);
    return made.server;
  };
  const asked = (server: ReturnType<typeof adminApi>["server"]) => server.sent.map((sent) => sent.query);

  it("is a table of which shop, who, why, from when to when, and how it ended", async () => {
    open();
    const table = await screen.findByRole("table", { name: "Yordam uchun kirish" });
    expect(cells(table)).toEqual([
      ["Do'kon", "Administrator", "Sabab", "Boshlangan", "Tugash vaqti", "Holat"],
      ["Baraka savdo", "a1b2c3 (siz)", "Egasi yordam so'radi", "2026-yil 6-oktabr, 11:00", "2026-yil 6-oktabr, 13:00", "Ochiq"],
      ["Ziyo market", "d4e5f6", "Egasi yordam so'radi", "2026-yil 6-oktabr, 11:00", "2026-yil 6-oktabr, 13:00", "Egasi tugatgan · 2026-yil 5-oktabr, 11:30"],
      ["Baraka savdo", "a1b2c3 (siz)", "Egasi yordam so'radi", "2026-yil 6-oktabr, 11:00", "2026-yil 6-oktabr, 13:00", "Administrator yopgan · 2026-yil 5-oktabr, 11:30"],
      ["Do'kon · 00aaaa", "a1b2c3 (siz)", "Egasi yordam so'radi", "2026-yil 6-oktabr, 11:00", "2026-yil 6-oktabr, 13:00", "Muddati tugagan"],
      ["Baraka savdo", "a1b2c3 (siz)", "Egasi yordam so'radi", "2026-yil 6-oktabr, 11:00", "2026-yil 6-oktabr, 13:00", "Yopilgan · 2026-yil 5-oktabr, 11:30"],
    ]);
    expect(within(table).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      `#/shops/${SHOP_ID}`,
      `#/shops/${OTHER_SHOP}`,
      `#/shops/${SHOP_ID}`,
      `#/shops/${SHOP_ID}`,
      `#/shops/${SHOP_ID}`,
    ]);
    // Opening and closing belong to a shop's page; nothing in the list changes anything.
    expect(table.querySelectorAll("button, input, select")).toHaveLength(0);
  });

  it("marks nobody as the reader while it does not know which administrator this is", async () => {
    open(null, NOBODY);
    const table = await screen.findByRole("table", { name: "Yordam uchun kirish" });
    expect(cells(table)[1]?.[1]).toBe("a1b2c3");
  });

  it("sends no filter at first, and narrows to the accesses open now", async () => {
    const server = open();
    await screen.findByRole("table");
    expect(asked(server)).toEqual([{}]);
    fireEvent.click(screen.getByLabelText("Faqat hozir ochiqlari"));
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ open: "true" }));
    fireEvent.click(screen.getByLabelText("Faqat hozir ochiqlari"));
    await waitFor(() => expect(asked(server).at(-1)).toEqual({}));
  });

  it("is narrowed to one shop by the address, and changes the address when another shop is typed", async () => {
    const server = open(SHOP_ID);
    await screen.findByRole("table");
    expect(asked(server)).toEqual([{ shop_id: SHOP_ID }]);
    const field = screen.getByLabelText("Do'kon ID (ixtiyoriy)") as HTMLInputElement;
    expect(field.value).toBe(SHOP_ID);
    fireEvent.change(field, { target: { value: OTHER_SHOP.toUpperCase() } });
    fireEvent.click(screen.getByRole("button", { name: "Qo'llash" }));
    expect(window.location.hash).toBe(`#/support-access/${OTHER_SHOP}`);
    fireEvent.change(field, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Qo'llash" }));
    expect(window.location.hash).toBe("#/support-access");
  });

  it("goes nowhere for something that is not a shop identifier", async () => {
    const server = open();
    fireEvent.change(await screen.findByLabelText("Do'kon ID (ixtiyoriy)"), { target: { value: "baraka" } });
    fireEvent.click(screen.getByRole("button", { name: "Qo'llash" }));
    expect(screen.getByText("Do'kon ID to'liq UUID bo'lishi kerak.").id).toBe("support-shop-error");
    expect(window.location.hash).toBe("");
    expect(asked(server)).toHaveLength(1);
  });

  it("reads the next page with the cursor, says so when there is nothing, and never writes", async () => {
    const server = open(null, KNOWN, (sent) =>
      sent.query["open"] === "true"
        ? ok({ items: [], next_cursor: null })
        : sent.query["cursor"] === "c2"
          ? ok({ items: [supportBody({ id: "s9", reason: "Ikkinchi sahifa" })], next_cursor: null })
          : ok({ items: [supportBody()], next_cursor: "c2" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Yana ko'rsatish" }));
    expect(await screen.findByText("Ikkinchi sahifa")).toBeTruthy();
    expect(asked(server).at(-1)).toEqual({ cursor: "c2" });
    fireEvent.click(screen.getByLabelText("Faqat hozir ochiqlari"));
    expect(await screen.findByText("Bu filtr bo'yicha kirish yo'q.")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
  });

  it("shows a failure with a retry", async () => {
    let fail = true;
    open(null, KNOWN, () => (fail ? "offline" : ok({ items: ROWS, next_cursor: null })));
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByRole("table")).toBeTruthy();
  });
});

describe("a shop's customers under an open support access", () => {
  const RECORDED = "Faqat o'qish uchun. Har bir ko'rishingiz yozib boriladi va do'kon egasiga ko'rsatiladi.";
  const VALI = customerBody({ id: "11111111-1111-4111-8111-111111111112", display_name: "Vali Aliyev", phone: null, status: "archived", balance: 0 });
  const list = (handler: (sent: Sent) => Reply = () => ok({ items: [customerBody(), VALI], next_cursor: null })) => {
    const made = adminApi(handler);
    renderAdmin(<CustomersScreen api={made.api} shopId={SHOP_ID} />);
    return made.server;
  };
  const page = (handler: (sent: Sent) => Reply = () => ok(detailBody({ overdue: { amount: 45000, since: "2026-10-01", days: 5, due_today: 0 }, entries_total: 7, entries: [entryBody(), entryBody({ id: "e2", kind: "payment", amount: 20000, note: "Naqd", promised_date: null, reversed: true, disputed: true })] }))) => {
    const made = adminApi(handler);
    renderAdmin(<CustomerScreen api={made.api} shopId={SHOP_ID} customerId={CUSTOMER_ID} />);
    return made.server;
  };

  it("says that every look is recorded and shown to the owner, and lists names, phones, state and debt", async () => {
    const server = list();
    expect(screen.getByRole("note").textContent).toBe(RECORDED);
    const table = await screen.findByRole("table", { name: "Do'kon mijozlari" });
    expect(cells(table)).toEqual([
      ["Mijoz", "Telefon", "Holat", "Qarzi"],
      ["Ali Valiyev", "+998901234567", "Faol", "120 000 so'm"],
      ["Vali Aliyev", "—", "Arxivlangan", "0 so'm"],
    ]);
    expect(within(table).getByRole("link", { name: "Ali Valiyev" }).getAttribute("href")).toBe(`#/shops/${SHOP_ID}/customers/${CUSTOMER_ID}`);
    expect(server.sent.map((sent) => [sent.method, sent.path, sent.query])).toEqual([["GET", CUSTOMERS, { status: "active" }]]);
    expect(screen.getByRole("link", { name: "Do'kon sahifasi" }).getAttribute("href")).toBe(`#/shops/${SHOP_ID}`);
  });

  it("is read-only: the only controls search and filter, and nothing is ever written", async () => {
    const server = list();
    const table = await screen.findByRole("table");
    expect(table.querySelectorAll("button, input, select, textarea")).toHaveLength(0);
    expect(screen.getAllByRole("button").map((button) => button.textContent)).toEqual(["Qidirish"]);
    expect(server.writes()).toHaveLength(0);
  });

  it("asks again only when a search is sent or the state is chosen, never while typing: each read is recorded", async () => {
    const server = list();
    await screen.findByRole("table");
    fireEvent.change(screen.getByLabelText("Ism yoki telefon bo'yicha qidirish"), { target: { value: "  ali   v " } });
    await pause(40);
    expect(server.sent).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Qidirish" }));
    await waitFor(() => expect(server.sent.at(-1)?.query).toEqual({ q: "ali v", status: "active" }));
    fireEvent.change(screen.getByLabelText("Holat"), { target: { value: "archived" } });
    await waitFor(() => expect(server.sent.at(-1)?.query).toEqual({ q: "ali v", status: "archived" }));
    expect(server.sent).toHaveLength(3);
  });

  it("reads the next page with the cursor, and says so when nobody matches", async () => {
    const server = list((sent) =>
      sent.query["q"] === "yo'q" ? ok({ items: [], next_cursor: null }) : sent.query["cursor"] === "c2" ? ok({ items: [VALI], next_cursor: null }) : ok({ items: [customerBody()], next_cursor: "c2" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Yana ko'rsatish" }));
    expect(await screen.findByText("Vali Aliyev")).toBeTruthy();
    expect(server.sent.at(-1)?.query).toEqual({ status: "active", cursor: "c2" });
    fireEvent.change(screen.getByLabelText("Ism yoki telefon bo'yicha qidirish"), { target: { value: "yo'q" } });
    fireEvent.click(screen.getByRole("button", { name: "Qidirish" }));
    expect(await screen.findByText("Mijoz topilmadi.")).toBeTruthy();
  });

  it("without an open access says what to do and shows nothing of the shop: no list, no search, no retry", async () => {
    const server = list(() => REQUIRED);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(
      "Bu do'kon uchun ochiq kirishingiz yo'q, shuning uchun do'kon ma'lumotlari ko'rsatilmaydi. Bu urinish auditga yozildi.Do'kon sahifasida sabab ko'rsatib kirish oching, so'ng shu yerga qayting.Do'kon sahifasi",
    );
    expect(within(alert).getByRole("link", { name: "Do'kon sahifasi" }).getAttribute("href")).toBe(`#/shops/${SHOP_ID}`);
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("search")).toBeNull();
    // A retry would only write another refused attempt into the audit.
    expect(screen.queryAllByRole("button")).toEqual([]);
    expect(screen.queryByRole("note")).toBeNull();
    await pause(30);
    expect(server.sent).toHaveLength(1);
  });

  it("shows any other failure with a retry", async () => {
    let fail = true;
    list(() => (fail ? "offline" : ok({ items: [customerBody()], next_cursor: null })));
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByText("Ali Valiyev")).toBeTruthy();
  });

  it("shows one customer: who they are, what they owe, and their entries, with the same statement", async () => {
    const server = page();
    expect(screen.getByRole("note").textContent).toBe(RECORDED);
    expect(await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" })).toBeTruthy();
    expect([...document.querySelectorAll("dl.facts > *")].map((part) => part.textContent?.replace(/\s/g, " "))).toEqual([
      "Telefon", "+998901234567",
      "Holat", "Faol",
      "Qarzi", "120 000 so'm",
      "Muddati o'tgan qarz", "45 000 so'm, kechikish 5 kun",
      "Nasiya chegarasi", "Do'konning umumiy chegarasi",
    ]);
    expect(screen.getByText("2 ta yozuv ko'rsatilgan, jami 7 ta.")).toBeTruthy();
    expect(cells(screen.getByRole("table", { name: "Yozuvlar" }))).toEqual([
      ["Vaqt", "Turi", "Summa", "To'lash muddati", "Izoh", "Belgi"],
      ["2026-yil 6-oktabr, 00:30", "Nasiya", "45 000 so'm", "2026-yil 5-noyabr", "—", "—"],
      ["2026-yil 6-oktabr, 00:30", "To'lov", "20 000 so'm", "—", "Naqd", "Bekor qilingan, E'tiroz bildirilgan"],
    ]);
    expect(server.sent.map((sent) => [sent.method, sent.path, sent.query])).toEqual([["GET", `${CUSTOMERS}/${CUSTOMER_ID}`, {}]]);
    expect(screen.getByRole("link", { name: "Mijozlar ro'yxati" }).getAttribute("href")).toBe(`#/shops/${SHOP_ID}/customers`);
  });

  it("offers nothing that records, reverses or changes: the customer's page has no control at all", async () => {
    const server = page();
    await screen.findByRole("table", { name: "Yozuvlar" });
    expect(document.querySelectorAll("button, input, select, textarea, form")).toHaveLength(0);
    expect(server.writes()).toHaveLength(0);
  });

  it("shows a customer's own limit, no overdue debt, and no entries when there are none", async () => {
    page(() => ok(detailBody({ credit_limit: 500000, entries: [], entries_total: 0 })));
    expect(await screen.findByText("Yozuv yo'q.")).toBeTruthy();
    const facts = [...document.querySelectorAll("dl.facts > dd")].map((part) => part.textContent?.replace(/\s/g, " "));
    expect(facts.slice(3)).toEqual(["—", "500 000 so'm"]);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("without an open access shows nothing of the customer, and says what to do", async () => {
    page(() => REQUIRED);
    expect((await screen.findByRole("alert")).textContent).toContain("Bu do'kon uchun ochiq kirishingiz yo'q");
    expect(screen.queryByRole("note")).toBeNull();
    expect(screen.queryByRole("heading", { level: 2 })).toBeNull();
    expect(screen.queryAllByRole("button")).toEqual([]);
    expect(screen.getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([`#/shops/${SHOP_ID}`]);
  });

  it("shows the not-found screen for a customer the shop does not have", async () => {
    page(() => refusal(404, "NOT_FOUND", "Topilmadi."));
    expect(await screen.findByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
  });

  it("says the statement in Russian too", () => {
    const made = adminApi(() => ok({ items: [], next_cursor: null }));
    renderAdmin(<CustomersScreen api={made.api} shopId={SHOP_ID} />, "ru");
    expect(screen.getByRole("note").textContent).toBe("Только для чтения. Каждый ваш просмотр записывается и показывается владельцу магазина.");
  });
});

describe("the audit, narrowed to one administrator", () => {
  const ROWS = [auditBody(), auditBody({ id: "a2", admin_id: OTHER_ADMIN, action: "support.customers_listed", reason: null, detail: { access_id: ACCESS_ID } })];
  const open = (shopId: string | null = null) => {
    const made = adminApi(() => ok({ items: ROWS, next_cursor: null }));
    const view = renderAdmin(<AuditScreen api={made.api} shopId={shopId} />);
    return { server: made.server, view, api: made.api };
  };
  const asked = (server: ReturnType<typeof adminApi>["server"]) => server.sent.map((sent) => sent.query);

  it("words what administrators did under a support access, and offers the group", async () => {
    open();
    const table = await screen.findByRole("table", { name: "Audit jurnali" });
    expect(cells(table)[2]?.[2]).toBe("Mijozlar ro'yxati ko'rildi");
    expect(within(screen.getByLabelText("Amal") as HTMLSelectElement).getByRole("option", { name: "Yordam uchun kirish" })).toBeTruthy();
    // Every action the server records under a support access has a word (application/support_access.py).
    const actions = ["opened", "closed", "refused", "customers_listed", "customer_viewed"].map((action) => `admin.action.support.${action}`);
    expect(actions.filter((key) => !(key in uzAdmin))).toEqual([]);
  });

  it("narrows to the administrator of a row, and no longer offers that on their rows", async () => {
    const { server } = open(SHOP_ID);
    const table = await screen.findByRole("table", { name: "Audit jurnali" });
    fireEvent.click(within(table).getAllByRole("button", { name: "Faqat shu administrator" })[1] as HTMLElement);
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ shop_id: SHOP_ID, admin_id: OTHER_ADMIN }));
    expect((screen.getByLabelText("Administrator ID (ixtiyoriy)") as HTMLInputElement).value).toBe(OTHER_ADMIN);
    const again = await screen.findByRole("table", { name: "Audit jurnali" });
    expect(within(again).getAllByRole("button", { name: "Faqat shu administrator" })).toHaveLength(1);
  });

  it("narrows to a typed identifier, combines it with the kind of action, and widens again when it is cleared", async () => {
    const { server } = open();
    const field = (await screen.findByLabelText("Administrator ID (ixtiyoriy)")) as HTMLInputElement;
    fireEvent.change(field, { target: { value: ` ${ADMIN_ID.toUpperCase()} ` } });
    fireEvent.click(screen.getByRole("button", { name: "Qo'llash" }));
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ admin_id: ADMIN_ID }));
    fireEvent.change(screen.getByLabelText("Amal"), { target: { value: "support" } });
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ admin_id: ADMIN_ID, action: "support" }));
    fireEvent.change(field, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Qo'llash" }));
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ action: "support" }));
  });

  it("asks nothing for something that is not an identifier", async () => {
    const { server } = open();
    fireEvent.change(await screen.findByLabelText("Administrator ID (ixtiyoriy)"), { target: { value: "a1b2c3" } });
    fireEvent.click(screen.getByRole("button", { name: "Qo'llash" }));
    expect(screen.getByText("Administrator ID to'liq UUID bo'lishi kerak.").id).toBe("audit-admin-error");
    await pause(30);
    expect(asked(server)).toEqual([{}]);
    expect(window.location.hash).toBe("");
  });

  it("follows the address to another shop and keeps the administrator it was narrowed to", async () => {
    const { server, view, api } = open(SHOP_ID);
    const table = await screen.findByRole("table", { name: "Audit jurnali" });
    fireEvent.click(within(table).getAllByRole("button", { name: "Faqat shu administrator" })[0] as HTMLElement);
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ shop_id: SHOP_ID, admin_id: ADMIN_ID }));
    view.rerender(
      <I18nProvider initialLanguage="uz">
        <AuditScreen api={api} shopId={OTHER_SHOP} />
      </I18nProvider>,
    );
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ shop_id: OTHER_SHOP, admin_id: ADMIN_ID }));
    expect((screen.getByLabelText("Do'kon ID (ixtiyoriy)") as HTMLInputElement).value).toBe(OTHER_SHOP);
  });
});

describe("the addresses of support access behind the door", () => {
  const heading = () => screen.getByRole("heading", { level: 1 }).textContent;
  const inside = (hash: string, handler: (sent: Sent) => Reply) => {
    window.location.hash = hash;
    const made = adminApi(handler);
    render(
      <I18nProvider initialLanguage="uz">
        <AdminRoutes api={made.api} now={now} />
      </I18nProvider>,
    );
    return made.server;
  };
  const current = () => within(screen.getByRole("navigation")).getAllByRole("link").filter((link) => link.getAttribute("aria-current") === "page").map((link) => link.textContent);

  it("opens the list of accesses in place of the placeholder", async () => {
    const server = inside("#/support-access", () => ok({ items: [supportBody()], next_cursor: null }));
    expect(heading()).toBe("Yordam uchun kirish");
    expect(await screen.findByRole("table", { name: "Yordam uchun kirish" })).toBeTruthy();
    expect(screen.queryByText("Bu bo'lim tez orada tayyor bo'ladi.")).toBeNull();
    expect(server.sent.map((sent) => sent.path)).toEqual([LIST]);
    expect(current()).toEqual(["Yordam uchun kirish"]);
    go(`#/support-access/${OTHER_SHOP}`);
    await waitFor(() => expect(server.sent.at(-1)?.query).toEqual({ shop_id: OTHER_SHOP }));
    expect(current()).toEqual(["Yordam uchun kirish"]);
  });

  it("opens a shop's customers and one customer under the shop, in the shops' section", async () => {
    const server = inside(`#/shops/${SHOP_ID}/customers`, (sent) => (sent.path === CUSTOMERS ? ok({ items: [customerBody()], next_cursor: null }) : ok(detailBody())));
    expect(heading()).toBe("Do'kon mijozlari");
    expect(await screen.findByRole("link", { name: "Ali Valiyev" })).toBeTruthy();
    expect(current()).toEqual(["Do'konlar"]);
    go(`#/shops/${SHOP_ID}/customers/${CUSTOMER_ID}`);
    expect(heading()).toBe("Mijoz");
    expect(await screen.findByRole("table", { name: "Yozuvlar" })).toBeTruthy();
    expect(server.sent.map((sent) => sent.path)).toEqual([CUSTOMERS, `${CUSTOMERS}/${CUSTOMER_ID}`]);
  });

  it("opens no malformed address, and asks nothing for one", async () => {
    const server = inside(`#/shops/${SHOP_ID}/customers/42`, () => ok({ items: [], next_cursor: null }));
    for (const hash of [`#/shops/${SHOP_ID}/customers/42`, `#/shops/${SHOP_ID}/customers/${CUSTOMER_ID}/entries`, "#/shops/42/customers", `#/shops/${SHOP_ID}/entries`, "#/support-access/42", `#/support-access/${SHOP_ID}/x`, "#/customers"]) {
      go(hash);
      expect(heading(), hash).toBe("Sahifa topilmadi");
    }
    await pause(30);
    expect(server.sent).toEqual([]);
  });

  it("remembers which administrator this is once an access is opened, for as long as the panel stays open", async () => {
    const open = { items: [] as unknown[] };
    const server = inside(`#/shops/${SHOP_ID}`, (sent) => {
      if (sent.path === LIST) {
        return ok({ items: open.items, next_cursor: null });
      }
      if (sent.method === "POST") {
        open.items = [supportBody(), others];
        return ok(supportBody(), 201);
      }
      return ok(shopDetailBody());
    });
    fireEvent.click(await screen.findByRole("button", { name: "Kirish ochish" }));
    fireEvent.change(screen.getByLabelText("Sabab"), { target: { value: "Egasi so'radi" } });
    fireEvent.click(screen.getByRole("button", { name: "Davom etish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, ochilsin" }));
    expect(await screen.findByText("Sizning kirishingiz · 2026-yil 6-oktabr, 13:00 gacha ochiq")).toBeTruthy();
    expect(screen.getByText("Administrator d4e5f6 · 2026-yil 6-oktabr, 13:00 gacha ochiq")).toBeTruthy();
    go("#/support-access");
    const table = await screen.findByRole("table", { name: "Yordam uchun kirish" });
    expect(cells(table).slice(1).map((row) => row[1])).toEqual(["a1b2c3 (siz)", "d4e5f6"]);
    // It is memory, not storage: nothing about it is kept by the browser.
    expect(JSON.stringify({ ...window.localStorage, ...window.sessionStorage })).not.toContain(ADMIN_ID);
    expect(server.writes()).toHaveLength(1);
  });
});
