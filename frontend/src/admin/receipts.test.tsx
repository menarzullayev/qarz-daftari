// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ruAdmin } from "../i18n/admin/ru";
import { uzAdmin } from "../i18n/admin/uz";
import { deferred, type Reply, type Sent } from "../testing/fakeServer";
import { ReceiptScreen } from "./ReceiptScreen";
import { ReceiptsScreen } from "./ReceiptsScreen";
import { cleanNote, parseReceiptMonths, RECEIPT_MONTHS_MAX, RECEIPT_STATUSES } from "./rules";
import { ShopScreen } from "./ShopScreen";
import {
  ADMIN,
  ADMIN_ID,
  adminApi,
  cells,
  CSRF,
  NOBODY,
  NOW,
  ok,
  OTHER_RECEIPT,
  OTHER_SHOP,
  RECEIPT_ID,
  receiptBody,
  receiptDetailBody,
  refusal,
  renderAdmin,
  SHOP_ID,
  shopDetailBody,
} from "./testing";

beforeEach(() => {
  window.location.hash = "";
});
afterEach(cleanup);

const QUEUE = `${ADMIN}/receipts`;
const ONE = `${QUEUE}/${RECEIPT_ID}`;
const KEY = /^[A-Za-z0-9_-]{8,128}$/;
const QUIET = 60;
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const approved = (overrides: Record<string, unknown> = {}) =>
  receiptBody({ status: "approved", months: 2, decided_at: "2026-10-06T07:00:00+00:00", decided_by: ADMIN_ID, ...overrides });
const rejected = (overrides: Record<string, unknown> = {}) =>
  receiptBody({ status: "rejected", reject_reason: "Summa mos kelmadi", decided_at: "2026-10-06T07:00:00+00:00", decided_by: ADMIN_ID, ...overrides });

describe("the administrator's receipt API", () => {
  it("lists by status with the cursor, reads one, and decides with a key, the CSRF token and only what was decided", async () => {
    const { api, server } = adminApi((sent) =>
      sent.method === "GET"
        ? sent.path === QUEUE
          ? ok({ items: [{ ...receiptBody(), copies: 2 }], next_cursor: "c2" })
          : ok(receiptDetailBody())
        : ok(sent.path.endsWith("/approve") ? { ...approved(), subscription: { state: "active", paid_through: "2026-12-05" } } : rejected()),
    );
    expect(await api.listReceipts({ status: "approved", cursor: "c1" })).toMatchObject({ items: [{ id: RECEIPT_ID, shopName: "Baraka savdo", statedAmount: 200000, statedMonths: 2, copies: 2, hasFile: true }], nextCursor: "c2" });
    expect(await api.readReceipt(RECEIPT_ID)).toMatchObject({ file: { url: "/files/receipt.token", expiresAt: "2026-10-06T07:05:00+00:00" }, copies: [] });
    expect(await api.approveReceipt(RECEIPT_ID, { months: 2, note: null }, "key-0000-0001")).toMatchObject({ status: "approved", subscription: { state: "active", paidThrough: "2026-12-05" } });
    await api.approveReceipt(RECEIPT_ID, { months: 3, note: "Ikki chek birga" }, "key-0000-0002");
    expect(await api.rejectReceipt(RECEIPT_ID, "Summa mos kelmadi", "key-0000-0003")).toMatchObject({ status: "rejected", subscription: null });
    expect(server.sent.map((sent) => [sent.method, sent.path, sent.query, sent.body, sent.headers["Idempotency-Key"], sent.headers["X-CSRF-Token"]])).toEqual([
      ["GET", QUEUE, { status: "approved", cursor: "c1" }, undefined, undefined, undefined],
      ["GET", ONE, {}, undefined, undefined, undefined],
      ["POST", `${ONE}/approve`, {}, { months: 2 }, "key-0000-0001", CSRF],
      ["POST", `${ONE}/approve`, {}, { months: 3, reason: "Ikki chek birga" }, "key-0000-0002", CSRF],
      ["POST", `${ONE}/reject`, {}, { reason: "Summa mos kelmadi" }, "key-0000-0003", CSRF],
    ]);
  });

  it("refuses answers that are not the contract, and months that are not a whole number", async () => {
    for (const wrong of [{ shop_name: null }, { has_file: "yes" }, { stated_amount: 1.5 }, { copies: "2" }]) {
      await expect(adminApi(() => ok({ items: [{ ...receiptBody(), copies: 0, ...wrong }], next_cursor: null })).api.listReceipts({})).rejects.toMatchObject({ code: "BAD_RESPONSE" });
    }
    await expect(adminApi(() => ok(receiptDetailBody({ file: { url: 7 } }))).api.readReceipt(RECEIPT_ID)).rejects.toMatchObject({ code: "BAD_RESPONSE" });
    const { api, server } = adminApi(() => ok(approved()));
    expect(() => api.approveReceipt(RECEIPT_ID, { months: 1.5, note: null }, "key-0000-0001")).toThrow(RangeError);
    expect(server.sent).toEqual([]);
  });

  it("knows the statuses, reads months as plain digits from 1 to 36, and a note as nothing or a reason of 3 to 500", () => {
    expect([RECEIPT_STATUSES, RECEIPT_MONTHS_MAX]).toEqual([["submitted", "approved", "rejected"], 36]);
    expect(["1", " 36 ", "0", "37", "2.5", "-3", "", "x", "1e1"].map(parseReceiptMonths)).toEqual([1, 36, null, null, null, null, null, null, null]);
    expect([cleanNote(""), cleanNote("   "), cleanNote("ab"), cleanNote("  ikki   chek "), cleanNote("x".repeat(501))]).toEqual([null, null, false, "ikki chek", false]);
    expect(cleanNote("x".repeat(500))).toHaveLength(500);
  });
});

describe("the queue of receipts", () => {
  const TWO = [
    { ...receiptBody({ created_at: "2026-10-05T04:00:00+00:00" }), copies: 0 },
    { ...receiptBody({ id: OTHER_RECEIPT, shop_id: OTHER_SHOP, shop_name: "Ziyo market", stated_amount: null, stated_months: null }), copies: 2 },
  ];
  const queue = (handler: (sent: Sent) => Reply = () => ok({ items: TWO, next_cursor: null })) => {
    const made = adminApi(handler);
    renderAdmin(<ReceiptsScreen api={made.api} />);
    return made.server;
  };

  it("is a table of the waiting receipts in the server's order, oldest first, each a link to the receipt, with copies marked", async () => {
    const server = queue();
    const table = await screen.findByRole("table", { name: "To'lov cheklari" });
    expect(cells(table)).toEqual([
      ["Yuborilgan", "Do'kon", "Ko'rsatilgan summa", "Ko'rsatilgan oylar", "Holat", "Shu fayl boshqa cheklarda"],
      ["2026-yil 5-oktabr, 09:00", "Baraka savdo", "200 000 so'm", "2", "Kutmoqda", "—"],
      ["2026-yil 6-oktabr, 11:00", "Ziyo market", "—", "—", "Kutmoqda", "Diqqat: yana 2 ta chekda"],
    ]);
    expect(within(table).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([`#/receipts/${RECEIPT_ID}`, `#/receipts/${OTHER_RECEIPT}`]);
    expect(screen.getByText("Eng avval yuborilgan chek birinchi turadi.")).toBeTruthy();
    expect(server.sent.map((sent) => [sent.method, sent.path, sent.query])).toEqual([["GET", QUEUE, { status: "submitted" }]]);
  });

  it("filters by status at once, reads the next page with the server's cursor and the same filter, and never writes", async () => {
    const server = queue((sent) => ok({ items: [{ ...approved({ id: sent.query["cursor"] ? OTHER_RECEIPT : RECEIPT_ID }), copies: 0 }], next_cursor: sent.query["cursor"] ? null : "next-1" }));
    await screen.findByRole("table");
    expect(within(screen.getByLabelText("Holat")).getAllByRole("option").map((option) => option.textContent)).toEqual(["Kutmoqda", "Tasdiqlangan", "Rad etilgan"]);
    fireEvent.change(screen.getByLabelText("Holat"), { target: { value: "approved" } });
    await waitFor(() => expect(server.sent.at(-1)?.query).toEqual({ status: "approved" }));
    fireEvent.click(await screen.findByRole("button", { name: "Yana ko'rsatish" }));
    await waitFor(() => expect(server.sent.at(-1)?.query).toEqual({ status: "approved", cursor: "next-1" }));
    await waitFor(() => expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(3));
    expect(server.writes()).toEqual([]);
  });

  it("says so when none waits, and shows a failure with a retry", async () => {
    let fail = true;
    queue(() => (fail ? refusal(500, "ERROR", "Xatolik yuz berdi.") : ok({ items: [], next_cursor: null })));
    expect(await screen.findByText("Xatolik yuz berdi.")).toBeTruthy();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByText("Bu holatda chek yo'q.")).toBeTruthy();
  });

  it("names no customer: no such column, and nothing but the shop and what its owner stated", async () => {
    queue();
    const table = await screen.findByRole("table");
    expect(table.textContent).not.toMatch(/mijoz|qarz|telefon/i);
  });
});

describe("one receipt", () => {
  /** A receipt behind a fake server: `read` is what GET answers now, `decide` answers a decision. */
  function receipt(read: unknown = receiptDetailBody(), decide: (sent: Sent) => Reply = (sent) => ok(sent.path.endsWith("/approve") ? { ...approved(), subscription: { state: "active", paid_through: "2026-12-05" } } : rejected())) {
    const held = { read: ok(read) as Reply };
    const made = adminApi((sent) => (sent.method === "GET" ? held.read : decide(sent)));
    renderAdmin(<ReceiptScreen api={made.api} receiptId={RECEIPT_ID} />);
    return { server: made.server, held };
  }
  const facts = () => [...document.querySelectorAll("dl.facts > *")].map((node) => node.textContent?.replace(/\s/g, " "));
  const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
  const press = (name: string) => fireEvent.click(screen.getByRole("button", { name }));
  const question = () => [...(screen.getByRole("group").querySelectorAll("p:not(.actions)") ?? [])].map((part) => part.textContent?.replace(/\s/g, " "));

  it("shows the shop, what its owner stated and the state, and records nothing but the look", async () => {
    const { server } = receipt();
    await screen.findByRole("button", { name: "Tasdiqlash" });
    expect(facts()).toEqual(["Do'kon", "Baraka savdo", "Ko'rsatilgan summa", "200 000 so'm", "Ko'rsatilgan oylar", "2", "Yuborilgan", "2026-yil 6-oktabr, 11:00", "Holat", "Kutmoqda"]);
    expect(screen.getByRole("link", { name: "Baraka savdo" }).getAttribute("href")).toBe(`#/shops/${SHOP_ID}`);
    expect(screen.getByRole("link", { name: "Cheklar navbati" }).getAttribute("href")).toBe("#/receipts");
    expect(server.sent.map((sent) => [sent.method, sent.path])).toEqual([["GET", ONE]]);
  });

  it("opens the file in two steps: the link came with the read, the person opens it, and a new one is a new read", async () => {
    const { server, held } = receipt();
    const link = await screen.findByRole("link", { name: "Chekni ochish" });
    expect([link.getAttribute("href"), link.getAttribute("target"), link.getAttribute("rel")]).toEqual(["/files/receipt.token", "_blank", "noopener noreferrer"]);
    expect(screen.getByText("Havola 5 daqiqa, 2026-yil 6-oktabr, 12:05 gacha amal qiladi. Ochilmasa, yangisini oling.")).toBeTruthy();
    held.read = ok(receiptDetailBody({ file: { url: "/files/second.token", expires_at: "2026-10-06T07:10:00+00:00" } }));
    press("Yangi havola olish");
    await waitFor(() => expect(screen.getByRole("link", { name: "Chekni ochish" }).getAttribute("href")).toBe("/files/second.token"));
    expect(server.sent.map((sent) => sent.method)).toEqual(["GET", "GET"]);
  });

  it("says when a receipt has no file, and when its file cannot be served now", async () => {
    receipt(receiptDetailBody({ has_file: false, file: null }));
    expect(await screen.findByText("Bu chekka fayl biriktirilmagan.")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Chekni ochish" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Yangi havola olish" })).toBeNull();
    cleanup();
    receipt(receiptDetailBody({ file: null }));
    expect(await screen.findByText("Faylni hozir ochib bo'lmaydi: saqlash muddati o'tgan yoki fayl ombori ishlamayapti.")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Chekni ochish" })).toBeNull();
    expect(screen.getByRole("button", { name: "Yangi havola olish" })).toBeTruthy();
  });

  it("warns when the same file was sent in other receipts and lists them; and says nothing of copies when there are none", async () => {
    receipt(
      receiptDetailBody({
        copies: [{ id: OTHER_RECEIPT, shop_id: OTHER_SHOP, shop_name: "Ziyo market", stated_amount: 100000, status: "approved", created_at: "2026-09-20T05:00:00+00:00" }],
      }),
    );
    const table = await screen.findByRole("table", { name: "Shu fayl yuborilgan boshqa cheklar" });
    expect(screen.getByRole("note").textContent).toBe("Diqqat: aynan shu fayl boshqa cheklarda ham yuborilgan. Qaror qabul qilishdan oldin ularni solishtiring.");
    expect(cells(table)).toEqual([
      ["Yuborilgan", "Do'kon", "Ko'rsatilgan summa", "Holat"],
      ["2026-yil 20-sentabr, 10:00", "Ziyo market", "100 000 so'm", "Tasdiqlangan"],
    ]);
    expect(within(table).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([`#/receipts/${OTHER_RECEIPT}`, `#/shops/${OTHER_SHOP}`]);
    cleanup();
    receipt();
    await screen.findByRole("button", { name: "Tasdiqlash" });
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("shows nothing of the shop's customers: no such heading, term or figure", async () => {
    const view = renderAdmin(<ReceiptScreen api={adminApi(() => ok(receiptDetailBody({ customers: [{ display_name: "Ali Valiyev", balance: 120000 }] }))).api} receiptId={RECEIPT_ID} />);
    await screen.findByRole("button", { name: "Tasdiqlash" });
    expect(view.container.textContent).not.toMatch(/mijoz|qarz|Ali Valiyev|120/i);
  });

  it("approves with the stated months prefilled: a question first, nothing on going back, one request with a key on yes", async () => {
    const { server } = receipt();
    press(await screen.findByRole("button", { name: "Tasdiqlash" }).then(() => "Tasdiqlash"));
    expect((screen.getByLabelText("Hisobga olinadigan oylar") as HTMLInputElement).value).toBe("2");
    press("Davom etish");
    expect(question()).toEqual(["Baraka savdo do'konining 200 000 so'm summali cheki tasdiqlansinmi? Hisobga olinadigan oylar: 2.", "Do'kon egasiga Telegram orqali xabar yuboriladi."]);
    press("Orqaga");
    await pause(QUIET);
    expect(server.writes()).toEqual([]);
    press("Davom etish");
    const yes = screen.getByRole("button", { name: "Ha, tasdiqlansin" });
    fireEvent.click(yes);
    fireEvent.click(yes);
    expect(await screen.findByText("Chek tasdiqlandi. Obuna 2026-yil 5-dekabr gacha to'langan.")).toBeTruthy();
    expect(server.writes().map((sent) => [sent.method, sent.path, sent.body])).toEqual([["POST", `${ONE}/approve`, { months: 2 }]]);
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(KEY);
    // Decided: the state says so, and there is nothing left to decide.
    expect(facts().slice(8)).toEqual(["Holat", "Tasdiqlangan", "Ko'rib chiqilgan", "2026-yil 6-oktabr, 12:00", "Kim ko'rib chiqqan", "a1b2c3", "Hisobga olingan oylar", "2"]);
    expect(screen.queryByRole("button", { name: "Tasdiqlash" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Rad etish" })).toBeNull();
  });

  it("sends the corrected months and the note, tidied, and repeats both in the question", async () => {
    const { server } = receipt();
    press(await screen.findByRole("button", { name: "Tasdiqlash" }).then(() => "Tasdiqlash"));
    type("Hisobga olinadigan oylar", "3");
    type("Izoh (ixtiyoriy)", "  Ikki   chek birga ");
    press("Davom etish");
    expect(question()).toEqual([
      "Baraka savdo do'konining 200 000 so'm summali cheki tasdiqlansinmi? Hisobga olinadigan oylar: 3.",
      "Izoh: Ikki chek birga",
      "Do'kon egasiga Telegram orqali xabar yuboriladi.",
    ]);
    press("Ha, tasdiqlansin");
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ months: 3, reason: "Ikki chek birga" });
  });

  it("asks no question while the months are not 1 to 36 or the note is neither empty nor 3 to 500 characters", async () => {
    const { server } = receipt(receiptDetailBody({ stated_months: null }));
    press(await screen.findByRole("button", { name: "Tasdiqlash" }).then(() => "Tasdiqlash"));
    expect((screen.getByLabelText("Hisobga olinadigan oylar") as HTMLInputElement).value).toBe("");
    for (const typed of ["", "0", "37", "1.5"]) {
      type("Hisobga olinadigan oylar", typed);
      press("Davom etish");
      expect(screen.getByText("Oylar soni 1 dan 36 gacha butun son bo'lishi kerak.")).toBeTruthy();
      expect(screen.queryByRole("group")).toBeNull();
    }
    type("Hisobga olinadigan oylar", "1");
    for (const typed of ["ab", "x".repeat(501)]) {
      type("Izoh (ixtiyoriy)", typed);
      press("Davom etish");
      expect(screen.getByText("Izoh 3 dan 500 belgigacha bo'lishi kerak yoki bo'sh qoldiriladi.")).toBeTruthy();
      expect(screen.queryByRole("group")).toBeNull();
    }
    await pause(QUIET);
    expect(server.writes()).toEqual([]);
  });

  it("rejects only with a reason of 3 to 500 characters: a question first, nothing on going back, one request with a key on yes", async () => {
    const { server } = receipt();
    press(await screen.findByRole("button", { name: "Rad etish" }).then(() => "Rad etish"));
    expect(screen.queryByLabelText("Hisobga olinadigan oylar")).toBeNull();
    for (const typed of ["", "ab", "x".repeat(501)]) {
      type("Sabab", typed);
      press("Davom etish");
      expect(screen.getByText("Sabab 3 dan 500 belgigacha bo'lishi kerak.")).toBeTruthy();
      expect(screen.queryByRole("group")).toBeNull();
    }
    type("Sabab", " Summa   mos kelmadi ");
    press("Davom etish");
    expect(question()).toEqual(["Baraka savdo do'konining 200 000 so'm summali cheki rad etilsinmi?", "Sabab: Summa mos kelmadi", "Do'kon egasiga Telegram orqali xabar yuboriladi."]);
    press("Orqaga");
    await pause(QUIET);
    expect(server.writes()).toEqual([]);
    press("Davom etish");
    press("Ha, rad etilsin");
    expect(await screen.findByText("Chek rad etildi.")).toBeTruthy();
    expect(server.writes().map((sent) => [sent.method, sent.path, sent.body])).toEqual([["POST", `${ONE}/reject`, { reason: "Summa mos kelmadi" }]]);
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(KEY);
    expect(facts().slice(-2)).toEqual(["Rad etish sababi", "Summa mos kelmadi"]);
    expect(screen.queryByRole("button", { name: "Rad etish" })).toBeNull();
  });

  it("disables the answer while the decision is in flight, so a double tap sends one", async () => {
    const gate = deferred<Exclude<Reply, Promise<unknown>>>();
    const { server } = receipt(receiptDetailBody(), () => gate.promise);
    press(await screen.findByRole("button", { name: "Tasdiqlash" }).then(() => "Tasdiqlash"));
    press("Davom etish");
    press("Ha, tasdiqlansin");
    await waitFor(() => expect((screen.getByRole("button", { name: "Saqlanmoqda…" }) as HTMLButtonElement).disabled).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Saqlanmoqda…" }));
    gate.resolve(ok({ ...approved(), subscription: null }));
    expect(await screen.findByText("Chek tasdiqlandi.")).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
  });

  it.each([
    ["approved", "Bu chek allaqachon tasdiqlangan. Hech narsa o'zgartirilmadi.", approved()],
    ["rejected", "Bu chek allaqachon rad etilgan. Hech narsa o'zgartirilmadi.", rejected()],
    ["withdrawn", "Bu chek bo'yicha qaror allaqachon qabul qilingan. Hech narsa o'zgartirilmadi.", approved()],
  ])("says a receipt was decided already (%s), reads it again, and offers no decision", async (status, text, now) => {
    const { server, held } = receipt(receiptDetailBody(), () => refusal(409, "RECEIPT_ALREADY_DECIDED", "Bu chek bo'yicha qaror allaqachon qabul qilingan.", { status }));
    press(await screen.findByRole("button", { name: "Rad etish" }).then(() => "Rad etish"));
    type("Sabab", "Summa mos kelmadi");
    press("Davom etish");
    held.read = ok(receiptDetailBody(now));
    press("Ha, rad etilsin");
    expect((await screen.findByRole("alert")).textContent).toBe(text);
    await waitFor(() => expect(server.sent.filter((sent) => sent.method === "GET")).toHaveLength(2));
    await waitFor(() => expect(facts()).toContain("Ko'rib chiqilgan"));
    expect(screen.getByRole("alert").textContent).toBe(text);
    expect(screen.queryByRole("button", { name: "Ha, rad etilsin" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Rad etish" })).toBeNull();
    expect(server.writes()).toHaveLength(1);
  });

  it("keeps the question open with the server's words for another refusal, and answers it again with the same key", async () => {
    const { server } = receipt(receiptDetailBody(), () => refusal(422, "VALIDATION", "Ma'lumot noto'g'ri.", { months: "must be between 1 and 36" }));
    press(await screen.findByRole("button", { name: "Tasdiqlash" }).then(() => "Tasdiqlash"));
    press("Davom etish");
    press("Ha, tasdiqlansin");
    expect((await screen.findByRole("alert")).textContent).toBe("Ma'lumot noto'g'ri.");
    press("Ha, tasdiqlansin");
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toBe(server.writes()[1]?.headers["Idempotency-Key"]);
    expect(screen.queryByText(/^Chek tasdiqlandi/)).toBeNull();
  });

  it.each([
    ["approved", approved()],
    ["rejected", rejected()],
    ["on_hold", receiptBody({ status: "on_hold" })],
  ])("offers no decision on a receipt that is %s", async (_, body) => {
    const { server } = receipt(receiptDetailBody(body));
    await screen.findByRole("link", { name: "Chekni ochish" });
    expect(screen.queryByRole("button", { name: "Tasdiqlash" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Rad etish" })).toBeNull();
    expect(server.writes()).toEqual([]);
  });

  it("shows the not-found screen for a receipt that does not exist, and a failure with a retry otherwise", async () => {
    renderAdmin(<ReceiptScreen api={adminApi(() => refusal(404, "NOT_FOUND", "Topilmadi.")).api} receiptId={RECEIPT_ID} />);
    expect(await screen.findByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    cleanup();
    let fail = true;
    renderAdmin(<ReceiptScreen api={adminApi(() => (fail ? refusal(500, "ERROR", "Xatolik yuz berdi.") : ok(receiptDetailBody()))).api} receiptId={RECEIPT_ID} />);
    expect(await screen.findByText("Xatolik yuz berdi.")).toBeTruthy();
    fail = false;
    press("Qayta urinish");
    expect(await screen.findByRole("button", { name: "Tasdiqlash" })).toBeTruthy();
  });

  it.each([
    ["uz", "Guruh administratori (Telegram ID 700123456)"],
    ["ru", "Администратор группы (Telegram ID 700123456)"],
  ] as const)("names the review group's administrator who decided by the Telegram identifier, in %s", async (language, name) => {
    // Decided from the review group by one of its Telegram administrators, who has no account here (DEC-064).
    const decided = receiptDetailBody({ status: "approved", months: 2, decided_at: "2026-10-06T07:00:00+00:00", decided_by: null, decided_by_tg_id: 700123456 });
    renderAdmin(<ReceiptScreen api={adminApi(() => ok(decided)).api} receiptId={RECEIPT_ID} />, language);
    await waitFor(() => expect(facts()).toContain(name));
    expect(facts()).not.toContain("a1b2c3");
    // An administrator of the platform is still named by the code, and nobody by a dash.
    cleanup();
    renderAdmin(<ReceiptScreen api={adminApi(() => ok({ ...decided, decided_by: ADMIN_ID, decided_by_tg_id: null })).api} receiptId={RECEIPT_ID} />, language);
    await waitFor(() => expect(facts()).toContain("a1b2c3"));
    expect(facts()).not.toContain(name);
  });

  it("refuses a Telegram identifier that is not a positive whole number", async () => {
    for (const wrong of ["700123456", 0, -5, 1.5]) {
      await expect(adminApi(() => ok(receiptDetailBody({ decided_by_tg_id: wrong }))).api.readReceipt(RECEIPT_ID)).rejects.toMatchObject({ code: "BAD_RESPONSE" });
    }
  });

  it("says the same in Russian", async () => {
    renderAdmin(<ReceiptScreen api={adminApi(() => ok(receiptDetailBody())).api} receiptId={RECEIPT_ID} />, "ru");
    fireEvent.click(await screen.findByRole("button", { name: "Подтвердить" }));
    fireEvent.click(screen.getByRole("button", { name: "Продолжить" }));
    expect(question()[0]).toBe("Подтвердить чек магазина «Baraka savdo» на сумму 200 000 сум? Засчитать месяцев: 2.");
  });
});

describe("a shop's receipts on the shop's page", () => {
  it("lists them, each a link to the receipt, with a waiting one worded", async () => {
    const waiting = { id: RECEIPT_ID, stated_amount: 200000, status: "submitted", months: null, reject_reason: null, created_at: "2026-10-06T06:00:00+00:00", decided_at: null };
    renderAdmin(<ShopScreen api={adminApi(() => ok(shopDetailBody({ receipts: [waiting] }))).api} shopId={SHOP_ID} now={() => NOW} who={NOBODY} />);
    const table = await screen.findByRole("table", { name: "Obuna to'lovi cheklari" });
    expect(cells(table)[1]).toEqual(["2026-yil 6-oktabr, 11:00", "200 000 so'm", "Kutmoqda", "—", "—", "—"]);
    expect(within(table).getByRole("link").getAttribute("href")).toBe(`#/receipts/${RECEIPT_ID}`);
  });
});

describe("the words for receipts in the administrator's catalog", () => {
  it("has a word for every status and every decided-already answer, in both languages", () => {
    const keys = [...RECEIPT_STATUSES.map((status) => `admin.receipt.${status}`), ...["approved", "rejected", "other"].map((status) => `admin.rc.decided.${status}`)];
    expect(keys.filter((key) => !(key in uzAdmin) || !(key in ruAdmin))).toEqual([]);
  });
});
