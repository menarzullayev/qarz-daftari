// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { deferred, type Reply, type Sent } from "../testing/fakeServer";
import { AuditScreen } from "./AuditScreen";
import { SettingsScreen } from "./SettingsScreen";
import { ShopScreen } from "./ShopScreen";
import { ShopsScreen } from "./ShopsScreen";
import {
  ADMIN,
  adminApi,
  auditBody,
  cells,
  CSRF,
  NOBODY,
  NOW,
  ok,
  OTHER_SHOP,
  platformBody,
  refusal,
  renderAdmin,
  SHOP_ID,
  shopBody,
  shopDetailBody,
} from "./testing";

beforeEach(() => {
  window.location.hash = "";
});
afterEach(cleanup);

const now = () => NOW;

describe("the list of shops", () => {
  const list = (handler: (sent: Sent) => Reply = () => ok({ items: [shopBody(), shopBody({ id: OTHER_SHOP, name: "Ziyo market", owner_tg_id: null }, { state: "active", stored_state: "active", trial_ends: null, paid_through: "2026-11-05" })], next_cursor: null })) => {
    const made = adminApi(handler);
    renderAdmin(<ShopsScreen api={made.api} />);
    return made.server;
  };
  const asked = (server: ReturnType<typeof adminApi>["server"]) => server.sent.map((sent) => sent.query);

  it("is a table of shops with their subscription and how many people they have, each a link to the shop", async () => {
    list();
    const table = await screen.findByRole("table", { name: "Do'konlar" });
    expect(cells(table)).toEqual([
      ["Do'kon", "Obuna holati", "Sinov tugaydi", "To'langan muddat", "Xodimlar soni", "Mijozlar soni", "Ochilgan"],
      ["Baraka savdo", "Sinov muddati", "2026-yil 20-oktabr", "—", "3", "42", "2026-yil 1-sentabr, 10:00"],
      ["Ziyo market", "Faol", "—", "2026-yil 5-noyabr", "3", "42", "2026-yil 1-sentabr, 10:00"],
    ]);
    expect(within(table).getByRole("link", { name: "Baraka savdo" }).getAttribute("href")).toBe(`#/shops/${SHOP_ID}`);
    expect(within(table).getAllByRole("rowheader").every((cell) => cell.getAttribute("scope") === "row")).toBe(true);
  });

  it("searches by name on request, tidied, and filters by subscription state at once", async () => {
    const server = list();
    await screen.findByRole("table");
    expect(asked(server)).toEqual([{}]);
    fireEvent.change(screen.getByLabelText("Do'kon nomi bo'yicha qidirish"), { target: { value: "  baraka   savdo " } });
    expect(asked(server)).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Qidirish" }));
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ q: "baraka savdo" }));
    const state = screen.getByLabelText("Obuna holati") as HTMLSelectElement;
    expect([...state.options].map((option) => [option.value, option.textContent])).toEqual([
      ["", "Barcha holatlar"],
      ["trial", "Sinov muddati"],
      ["active", "Faol"],
      ["limited", "Cheklangan"],
      ["suspended", "To'xtatilgan"],
    ]);
    fireEvent.change(state, { target: { value: "suspended" } });
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ q: "baraka savdo", state: "suspended" }));
  });

  it("reads the next page with the server's cursor and keeps the filters", async () => {
    const server = list((sent) =>
      sent.query["cursor"] === "c2"
        ? ok({ items: [shopBody({ id: OTHER_SHOP, name: "Ziyo market" })], next_cursor: null })
        : ok({ items: [shopBody()], next_cursor: "c2" }),
    );
    fireEvent.change(await screen.findByLabelText("Obuna holati"), { target: { value: "trial" } });
    fireEvent.click(await screen.findByRole("button", { name: "Yana ko'rsatish" }));
    expect(await screen.findByText("Ziyo market")).toBeTruthy();
    expect(asked(server).at(-1)).toEqual({ state: "trial", cursor: "c2" });
    expect(screen.queryByRole("button", { name: "Yana ko'rsatish" })).toBeNull();
  });

  it("says so when there is none or none matches, and shows a failure with a retry", async () => {
    let mode: "empty" | "fail" | "ok" = "empty";
    list(() => (mode === "empty" ? ok({ items: [], next_cursor: null }) : mode === "fail" ? "offline" : ok({ items: [shopBody()], next_cursor: null })));
    expect(await screen.findByText("Hali birorta do'kon yo'q.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Obuna holati"), { target: { value: "limited" } });
    expect(await screen.findByText("Bu qidiruv bo'yicha do'kon topilmadi.")).toBeTruthy();
    mode = "fail";
    fireEvent.change(screen.getByLabelText("Obuna holati"), { target: { value: "" } });
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    mode = "ok";
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByRole("table")).toBeTruthy();
  });
});

describe("one shop", () => {
  type Held = { detail: unknown };
  const open = (detail: unknown = shopDetailBody(), onWrite: (sent: Sent) => Reply = () => ok(shopBody())) => {
    const held: Held = { detail };
    const made = adminApi((sent) =>
      // The page also reads the shop's open support accesses; these tests are about the rest of it.
      sent.path === `${ADMIN}/support-access` ? ok({ items: [], next_cursor: null }) : sent.method === "GET" ? ok(held.detail) : onWrite(sent),
    );
    renderAdmin(<ShopScreen api={made.api} shopId={SHOP_ID} now={now} who={NOBODY} />);
    return { server: made.server, held };
  };
  const buttons = () => within(screen.getByRole("region", { name: "Obuna" })).getAllByRole("button").map((button) => button.textContent);
  const start = async (action: string, reason = "Egasi so'radi", date?: string) => {
    fireEvent.click(await screen.findByRole("button", { name: action }));
    if (date !== undefined) {
      fireEvent.change(screen.getByLabelText(/oxirgi kun/), { target: { value: date } });
    }
    fireEvent.change(screen.getByLabelText("Sabab"), { target: { value: reason } });
    fireEvent.click(screen.getByRole("button", { name: "Davom etish" }));
  };

  it("shows the shop, its subscription, its counts, the receipts and the changes administrators made", async () => {
    const { server } = open();
    expect(await screen.findByRole("heading", { level: 2, name: "Baraka savdo" })).toBeTruthy();
    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${ADMIN}/shops/${SHOP_ID}` });
    const facts = [...document.querySelectorAll("dl.facts")].map((list) => [...list.children].map((part) => part.textContent));
    expect(facts[0]).toEqual([
      "Do'kon holati", "Ishlamoqda",
      "Til", "uz",
      "Ochilgan", "2026-yil 1-sentabr, 10:00",
      "Egasining Telegram ID raqami", "123456789",
      "Xodimlar soni", "3",
      "Mijozlar soni", "42",
    ]);
    expect(facts[1]).toEqual(["Obuna holati", "Sinov muddati", "Sinov tugaydi", "2026-yil 20-oktabr", "To'langan muddat", "—"]);
    expect(cells(screen.getByRole("table", { name: "Obuna to'lovi cheklari" }))).toEqual([
      ["Yuborilgan", "Ko'rsatilgan summa", "Holat", "Oylar", "Ko'rib chiqilgan", "Rad etish sababi"],
      ["2026-yil 20-sentabr, 10:00", "100 000 so'm", "Tasdiqlangan", "1", "2026-yil 20-sentabr, 11:00", "—"],
    ]);
    expect(cells(screen.getByRole("table", { name: "Obuna o'zgarishlari tarixi" }))).toEqual([
      ["Vaqt", "Amal", "Oldin", "Keyin", "Sabab", "Administrator"],
      ["2026-yil 5-oktabr, 11:00", "Sinov muddati belgilandi", "Sinov muddati, sinov 2026-yil 1-oktabr gacha", "Sinov muddati, sinov 2026-yil 20-oktabr gacha", "Egasi so'radi", "a1b2c3"],
    ]);
    expect(screen.getByRole("link", { name: "Shu do'kon bo'yicha audit" }).getAttribute("href")).toBe(`#/audit/${SHOP_ID}`);
  });

  it("shows nothing of the shop's customers or entries: no such heading, column or figure", async () => {
    open();
    await screen.findByRole("heading", { level: 2, name: "Baraka savdo" });
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/Qarzdorlar|Yozuvlar|Jami qarz|Nasiya|Mijozlar ro'yxati/);
    // The one thing about customers is how many there are.
    expect(text.match(/Mijoz/g)).toEqual(["Mijoz"]);
    expect(screen.queryByRole("link", { name: /Mijoz/ })).toBeNull();
  });

  it("says when the stored state lags behind, what a suspension interrupted, and when a shop is to be deleted", async () => {
    open(shopDetailBody({ status: "deletion_pending", deletion_due: "2026-11-05T07:00:00+00:00", receipts: [], changes: [] }, { state: "suspended", stored_state: "suspended", prior_state: "active" }));
    expect(await screen.findByText("To'xtatilishidan oldin: Faol")).toBeTruthy();
    expect(screen.getByText("O'chirish so'ralgan")).toBeTruthy();
    expect(screen.getByText("2026-yil 5-noyabr, 12:00 da o'chiriladi")).toBeTruthy();
    expect(screen.getByText("Chek yuborilmagan.")).toBeTruthy();
    expect(screen.getByText("Administratorlar bu do'kon obunasini o'zgartirmagan.")).toBeTruthy();
    cleanup();
    open(shopDetailBody({}, { state: "limited", stored_state: "trial" }));
    expect(await screen.findByText("Yozuvda: Sinov muddati (kunlik tekshiruvgacha)")).toBeTruthy();
  });

  it.each([
    ["trial", "trial", ["Sinov muddatini belgilash", "Sinovni tugatish", "To'langan muddatni belgilash", "Do'konni to'xtatish"]],
    ["limited", "limited", ["Sinov muddatini belgilash", "To'langan muddatni belgilash", "Do'konni to'xtatish"]],
    ["active", "active", ["To'langan muddatni belgilash", "Do'konni to'xtatish"]],
    ["suspended", "suspended", ["To'xtatishni bekor qilish"]],
  ])("offers a %s shop the changes that apply to it", async (state, stored, offered) => {
    open(shopDetailBody({}, { state, stored_state: stored }));
    await screen.findByRole("heading", { level: 2, name: "Baraka savdo" });
    expect(buttons()).toEqual(offered);
  });

  it("needs a reason of 3 to 500 characters before the question is even asked", async () => {
    const { server } = open();
    await start("Do'konni to'xtatish", "ab");
    expect(screen.getByText("Sabab 3 dan 500 belgigacha bo'lishi kerak.").id).toBe("change-reason-error");
    expect(screen.getByLabelText("Sabab").getAttribute("aria-invalid")).toBe("true");
    expect(screen.queryByRole("button", { name: "Ha, bajarilsin" })).toBeNull();
    expect(server.writes()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Bekor qilish" }));
    expect(buttons()).toContain("Do'konni to'xtatish");
  });

  it("asks before suspending, sends nothing on going back, and one request with a key and the reason on yes", async () => {
    const { server, held } = open(undefined, () => ok(shopBody({}, { state: "suspended", stored_state: "suspended", prior_state: "trial" })));
    await start("Do'konni to'xtatish", "  To'lov   qilinmadi ");
    expect(screen.getByText("Baraka savdo: «Do'konni to'xtatish» bajarilsinmi?")).toBeTruthy();
    expect(screen.getByText("Sabab: To'lov qilinmadi")).toBeTruthy();
    expect(screen.getByText("Do'kon egasiga sababi bilan xabar yuboriladi.")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Orqaga" }));
    expect(server.writes()).toHaveLength(0);
    expect((screen.getByLabelText("Sabab") as HTMLTextAreaElement).value).toBe("  To'lov   qilinmadi ");

    fireEvent.click(screen.getByRole("button", { name: "Davom etish" }));
    held.detail = shopDetailBody({}, { state: "suspended", stored_state: "suspended", prior_state: "trial" });
    fireEvent.click(screen.getByRole("button", { name: "Ha, bajarilsin" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Bajarildi: Do'konni to'xtatish."));
    expect(server.writes()).toHaveLength(1);
    const sent = server.writes()[0];
    expect(sent).toMatchObject({ method: "POST", path: `${ADMIN}/shops/${SHOP_ID}/suspend`, body: { reason: "To'lov qilinmadi" } });
    expect(sent?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(sent?.headers["X-CSRF-Token"]).toBe(CSRF);
    // The page shows what the server answered, and the only change now offered is the way back.
    await waitFor(() => expect(buttons()).toEqual(["To'xtatishni bekor qilish"]));
    expect(screen.getByText("To'xtatilishidan oldin: Sinov muddati")).toBeTruthy();
    // The shop was read again once, for its history.
    expect(server.sent.filter((s) => s.method === "GET" && s.path === `${ADMIN}/shops/${SHOP_ID}`)).toHaveLength(2);
  });

  it.each([
    ["Sinov muddatini belgilash", "Sinovning oxirgi kuni", "2026-10-06", "2027-10-06", "2026-10-25", "trial", { trial_ends: "2026-10-25" }],
    ["To'langan muddatni belgilash", "To'langan oxirgi kun", "2026-10-05", "2029-10-08", "2026-11-06", "paid-through", { paid_through: "2026-11-06" }],
  ])("%s takes a day within its range and sends it with the reason", async (action, label, min, max, date, path, body) => {
    const { server } = open();
    fireEvent.click(await screen.findByRole("button", { name: action }));
    const field = screen.getByLabelText(label) as HTMLInputElement;
    expect([field.type, field.min, field.max]).toEqual(["date", min, max]);
    // No day, and a day outside the range: nothing goes further.
    fireEvent.change(screen.getByLabelText("Sabab"), { target: { value: "Egasi so'radi" } });
    fireEvent.click(screen.getByRole("button", { name: "Davom etish" }));
    expect(document.getElementById("change-date-error")?.textContent).toMatch(/gacha bo'lgan kunni tanlang/);
    fireEvent.change(field, { target: { value: "2030-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Davom etish" }));
    expect(screen.queryByRole("button", { name: "Ha, bajarilsin" })).toBeNull();
    expect(server.writes()).toHaveLength(0);

    fireEvent.change(field, { target: { value: date } });
    fireEvent.click(screen.getByRole("button", { name: "Davom etish" }));
    expect(screen.getByText(/^Sana: 2026-yil/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Ha, bajarilsin" }));
    await screen.findByRole("status");
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${ADMIN}/shops/${SHOP_ID}/${path}`, body: { reason: "Egasi so'radi", ...body } });
  });

  it("ends a trial and lifts a suspension with a reason alone", async () => {
    const trial = open();
    await start("Sinovni tugatish");
    expect(screen.queryByText(/^Sana:/)).toBeNull();
    expect(screen.queryByText("Do'kon egasiga sababi bilan xabar yuboriladi.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Ha, bajarilsin" }));
    await screen.findByRole("status");
    expect(trial.server.writes()[0]).toMatchObject({ path: `${ADMIN}/shops/${SHOP_ID}/trial/end`, body: { reason: "Egasi so'radi" } });
    cleanup();

    const suspended = open(shopDetailBody({}, { state: "suspended", stored_state: "suspended" }));
    await start("To'xtatishni bekor qilish");
    expect(screen.getByText("Do'kon egasiga sababi bilan xabar yuboriladi.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Ha, bajarilsin" }));
    await screen.findByRole("status");
    expect(suspended.server.writes()[0]).toMatchObject({ path: `${ADMIN}/shops/${SHOP_ID}/unsuspend`, body: { reason: "Egasi so'radi" } });
  });

  it("disables the answer while the change is in flight, so a double tap sends one", async () => {
    const gate = deferred<ReturnType<typeof ok>>();
    const { server } = open(undefined, () => gate.promise);
    await start("Do'konni to'xtatish");
    const yes = screen.getByRole("button", { name: "Ha, bajarilsin" });
    fireEvent.click(yes);
    fireEvent.click(yes);
    expect(((await screen.findByRole("button", { name: "Saqlanmoqda…" })) as HTMLButtonElement).disabled).toBe(true);
    gate.resolve(ok(shopBody()));
    await screen.findByRole("status");
    expect(server.writes()).toHaveLength(1);
  });

  it.each([
    ["already_suspended", "Do'kon allaqachon to'xtatilgan."],
    ["not_suspended", "Do'kon to'xtatilmagan."],
    ["suspended", "Do'kon to'xtatilgan: avval to'xtatishni bekor qiling."],
    ["paid", "Do'kon to'lagan muddatida: sinov muddati qo'llanmaydi."],
    ["date_out_of_range", "Sana ruxsat etilgan oraliqdan tashqarida."],
    ["not_in_trial", "Do'kon sinov muddatida emas."],
    ["not_paid", "To'langan muddati yo'q do'konda uni kechagi kun bilan tugatib bo'lmaydi."],
  ])("explains the server's refusal %s, and a retry resends the same key", async (reason, detail) => {
    const general = "Obunaning hozirgi holatida bu o'zgarishni qilib bo'lmaydi.";
    const { server } = open(undefined, () => refusal(409, "SUBSCRIPTION_CHANGE_REFUSED", general, { reason }));
    await start("Do'konni to'xtatish");
    fireEvent.click(screen.getByRole("button", { name: "Ha, bajarilsin" }));
    expect((await screen.findByRole("alert")).textContent).toContain(general);
    expect(screen.getByText(detail)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Ha, bajarilsin" }));
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[1]?.headers["Idempotency-Key"]).toBe(server.writes()[0]?.headers["Idempotency-Key"]);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows the not-found screen for a shop that does not exist, and a failure with a retry otherwise", async () => {
    const missing = adminApi(() => refusal(404, "NOT_FOUND", "Topilmadi."));
    renderAdmin(<ShopScreen api={missing.api} shopId={SHOP_ID} now={now} who={NOBODY} />);
    expect(await screen.findByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    cleanup();
    let fail = true;
    const flaky = adminApi(() => (fail ? "offline" : ok(shopDetailBody())));
    renderAdmin(<ShopScreen api={flaky.api} shopId={SHOP_ID} now={now} who={NOBODY} />);
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Baraka savdo" })).toBeTruthy();
  });
});

describe("platform settings", () => {
  const open = (onWrite: (sent: Sent) => Reply = (sent) => ok(platformBody({}, (sent.body as { changes: Record<string, unknown> }).changes)), loaded: unknown = platformBody()) => {
    const made = adminApi((sent) => (sent.method === "GET" ? ok(loaded) : onWrite(sent)));
    renderAdmin(<SettingsScreen api={made.api} />);
    return made.server;
  };
  const save = () => fireEvent.click(screen.getByRole("button", { name: "Saqlash" }));
  const DAYS = "Sinov muddati, kun";
  const PRICE = "Oylik obuna narxi, so'm";
  const CODE = "Autentifikator kodi";

  it("shows each setting with its value, its type and range, whether it asks for a code, and its last change", async () => {
    open();
    const days = (await screen.findByLabelText(DAYS)) as HTMLInputElement;
    expect(days.value).toBe("30");
    expect(document.getElementById("setting-trial_days-hint")?.textContent).toBe("1 dan 365 gacha butun son.");
    expect(document.getElementById("setting-price_uzs-hint")?.textContent).toBe(
      "1000 dan 10000000 gacha butun son. O'zgartirish uchun autentifikator kodi qayta so'raladi.",
    );
    expect(document.getElementById("setting-payment_cards-hint")?.textContent).toBe(
      "10 tagacha karta: har biriga nom va 16 ta raqam. Birinchisi — asosiy karta; ro'yxat bo'sh bo'lsa, to'lov uchun karta ko'rsatilmaydi. O'zgartirish uchun autentifikator kodi qayta so'raladi.",
    );
    expect(document.getElementById("setting-review_group-hint")?.textContent).toContain("manfiy son");
    expect(document.getElementById("setting-sms_monthly_quota-hint")?.textContent).toBe("0 dan 100000 gacha butun son.");
    expect((screen.getByLabelText("Yangi do'konlarga sinov muddati beriladi") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("SMS yoqilgan") as HTMLInputElement).type).toBe("checkbox");
    const cards = screen.getByRole("group", { name: "To'lov qabul qilinadigan kartalar" });
    expect((within(cards).getByLabelText("1-karta nomi") as HTMLInputElement).value).toBe("Humo · Anorbank");
    expect((within(cards).getByLabelText("1-karta raqami") as HTMLInputElement).value).toBe("8600123456789012");
    expect(screen.getByText("Oxirgi o'zgarish: 2026-yil 1-oktabr, 10:00, administrator a1b2c3.")).toBeTruthy();
    // Nothing differs yet, so no code is asked for.
    expect(screen.queryByLabelText(CODE)).toBeNull();
  });

  it("sends nothing when nothing was changed", async () => {
    const server = open();
    await screen.findByLabelText(DAYS);
    save();
    expect(screen.getByText("Hech narsa o'zgartirilmagan.")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
  });

  it("changes a plain setting without a code: only what changed is sent, with a key, and what changed is shown", async () => {
    const server = open();
    fireEvent.change(await screen.findByLabelText(DAYS), { target: { value: "14" } });
    expect(screen.queryByLabelText(CODE)).toBeNull();
    save();
    const done = await screen.findByRole("status");
    expect(done.textContent).toContain("Sozlamalar saqlandi. O'zgarganlari:");
    expect(within(done).getByText("Sinov muddati, kun: 30 → 14")).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "PATCH", path: `${ADMIN}/settings`, body: { changes: { trial_days: 14 } } });
    expect(server.writes()[0]?.body).toEqual({ changes: { trial_days: 14 } });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(server.writes()[0]?.headers["X-CSRF-Token"]).toBe(CSRF);
  });

  it.each([
    [DAYS, "0", "1 dan 365 gacha butun son."],
    [DAYS, "366", "1 dan 365 gacha butun son."],
    [PRICE, "999", "1000 dan 10000000 gacha butun son."],
    ["Cheklar ko'rib chiqiladigan guruh (chat ID)", "12345", "Telegram guruhining chat ID raqami (manfiy son); bo'sh qoldirilsa, o'chiriladi."],
  ])("sends nothing while %s is %j, and says what it must be next to the field", async (label, value, message) => {
    const server = open();
    const field = await screen.findByLabelText(label);
    fireEvent.change(field, { target: { value } });
    save();
    expect(document.getElementById(`${field.id}-error`)?.textContent).toBe(message);
    expect(field.getAttribute("aria-invalid")).toBe("true");
    expect(server.writes()).toHaveLength(0);
  });

  it("asks for the code in the same form as soon as a sensitive setting differs, and not before", async () => {
    const server = open();
    const price = await screen.findByLabelText(PRICE);
    fireEvent.change(price, { target: { value: "120000" } });
    expect(screen.getByLabelText(CODE)).toBeTruthy();
    expect(screen.getByLabelText(CODE).closest("form")).toBe(price.closest("form"));
    save();
    expect(screen.getByText("Kod aynan 6 ta raqamdan iborat.").id).toBe("settings-code-error");
    expect(server.writes()).toHaveLength(0);
    // Put back as it was, the change is gone and so is the question.
    fireEvent.change(price, { target: { value: "100000" } });
    expect(screen.queryByLabelText(CODE)).toBeNull();
  });

  it("sends a sensitive change with the code and the reason, then empties the code", async () => {
    const server = open();
    fireEvent.change(await screen.findByLabelText(PRICE), { target: { value: "120 000" } });
    fireEvent.click(screen.getByLabelText("SMS yoqilgan"));
    fireEvent.change(screen.getByLabelText(CODE), { target: { value: "123456" } });
    fireEvent.change(screen.getByLabelText("Sabab (ixtiyoriy)"), { target: { value: " Narx   oshdi " } });
    save();
    const done = await screen.findByRole("status");
    expect(within(done).getByText("Oylik obuna narxi, so'm: 100000 → 120000")).toBeTruthy();
    expect(within(done).getByText("SMS yoqilgan: o'chirilgan → yoqilgan")).toBeTruthy();
    expect(server.writes()[0]?.body).toEqual({ changes: { price_uzs: 120000, sms_on: true }, code: "123456", reason: "Narx oshdi" });
    expect(screen.queryByLabelText(CODE)).toBeNull();
    // A code is used once: the next sensitive change starts with an empty field, not the old code.
    fireEvent.change(screen.getByLabelText(PRICE), { target: { value: "130000" } });
    expect((screen.getByLabelText(CODE) as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Sabab (ixtiyoriy)") as HTMLTextAreaElement).value).toBe("");
  });

  it("clears the cards by removing the last one, and a group with an empty field", async () => {
    const server = open(undefined, platformBody({}, { review_group: -1001234567890 }));
    fireEvent.click(await screen.findByRole("button", { name: "1-kartani olib tashlash" }));
    expect(screen.getByText("Karta kiritilmagan.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Cheklar ko'rib chiqiladigan guruh (chat ID)"), { target: { value: " " } });
    fireEvent.change(screen.getByLabelText(CODE), { target: { value: "123456" } });
    save();
    const done = await screen.findByRole("status");
    // What changed is said by name and last four digits: the whole number is not repeated in the notice.
    expect(within(done).getByText("To'lov qabul qilinadigan kartalar: Humo · Anorbank ··9012 → kiritilmagan")).toBeTruthy();
    expect(done.textContent).not.toContain("8600123456789012");
    expect(server.writes()[0]?.body).toEqual({ changes: { payment_cards: [], review_group: null }, code: "123456" });
  });

  describe("the cards to pay to", () => {
    const HUMO = { number: "8600123456789012", label: "Humo · Anorbank" };
    const UZCARD = { number: "5614681234567890", label: "Uzcard · Kapitalbank" };
    const VISA = { number: "4278310012345678", label: "Visa · Ipak Yo'li" };
    const withCards = (...cards: unknown[]) => platformBody({}, { payment_cards: cards });
    const press = (name: string) => fireEvent.click(screen.getByRole("button", { name }));
    const fill = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
    const rows = () =>
      within(screen.getByRole("group", { name: "To'lov qabul qilinadigan kartalar" }))
        .getAllByRole("listitem")
        .map((row) => [...row.querySelectorAll("input")].map((input) => input.value));
    const sentCards = (server: ReturnType<typeof open>) => (server.writes()[0]?.body as { changes: { payment_cards: unknown } }).changes.payment_cards;

    it("adds a card: nothing is asked or sent until it differs, and it is sent with the code as the server stores it", async () => {
      const server = open();
      await screen.findByLabelText(DAYS);
      expect(screen.queryByLabelText(CODE)).toBeNull();
      press("Karta qo'shish");
      fill("2-karta nomi", "  Uzcard · Kapitalbank ");
      fill("2-karta raqami", "5614 6812 3456 7890");
      fireEvent.change(screen.getByLabelText(CODE), { target: { value: "123456" } });
      save();
      const done = await screen.findByRole("status");
      expect(within(done).getByText("To'lov qabul qilinadigan kartalar: Humo · Anorbank ··9012 → Humo · Anorbank ··9012, Uzcard · Kapitalbank ··7890")).toBeTruthy();
      expect(server.writes()).toHaveLength(1);
      expect(server.writes()[0]?.body).toEqual({ changes: { payment_cards: [HUMO, UZCARD] }, code: "123456" });
      // The answer is what the editor now holds.
      expect(rows()).toEqual([
        ["Humo · Anorbank", "8600123456789012"],
        ["Uzcard · Kapitalbank", "5614681234567890"],
      ]);
    });

    it("marks the first card as the primary and moves cards up and down: the order is what is sent", async () => {
      const server = open(undefined, withCards(HUMO, UZCARD, VISA));
      await screen.findByLabelText(DAYS);
      const first = () => screen.getByRole("listitem", { name: "1-karta" });
      expect(within(first()).getByText("Asosiy karta")).toBeTruthy();
      expect(screen.getAllByText("Asosiy karta")).toHaveLength(1);
      expect((screen.getByRole("button", { name: "1-kartani yuqoriga" }) as HTMLButtonElement).disabled).toBe(true);
      expect((screen.getByRole("button", { name: "3-kartani pastga" }) as HTMLButtonElement).disabled).toBe(true);
      // Nothing differs yet.
      expect(screen.queryByLabelText(CODE)).toBeNull();

      press("3-kartani yuqoriga");
      press("2-kartani yuqoriga");
      expect(rows().map((row) => row[0])).toEqual(["Visa · Ipak Yo'li", "Humo · Anorbank", "Uzcard · Kapitalbank"]);
      expect(within(first()).getByLabelText("1-karta nomi")).toHaveProperty("value", "Visa · Ipak Yo'li");
      press("2-kartani pastga");
      fireEvent.change(screen.getByLabelText(CODE), { target: { value: "123456" } });
      save();
      await screen.findByRole("status");
      expect(sentCards(server)).toEqual([VISA, UZCARD, HUMO]);
    });

    it("asks for no code when the cards are moved back to where they were", async () => {
      const server = open(undefined, withCards(HUMO, UZCARD));
      await screen.findByLabelText(DAYS);
      press("1-kartani pastga");
      expect(screen.getByLabelText(CODE)).toBeTruthy();
      press("1-kartani pastga");
      expect(screen.queryByLabelText(CODE)).toBeNull();
      save();
      expect(screen.getByText("Hech narsa o'zgartirilmagan.")).toBeTruthy();
      expect(server.writes()).toHaveLength(0);
    });

    it("removes a card from the middle and keeps the others in order", async () => {
      const server = open(undefined, withCards(HUMO, UZCARD, VISA));
      await screen.findByLabelText(DAYS);
      press("2-kartani olib tashlash");
      expect(rows().map((row) => row[0])).toEqual(["Humo · Anorbank", "Visa · Ipak Yo'li"]);
      fireEvent.change(screen.getByLabelText(CODE), { target: { value: "123456" } });
      save();
      await screen.findByRole("status");
      expect(sentCards(server)).toEqual([HUMO, VISA]);
    });

    it.each([
      ["a number of fifteen digits", "Uzcard", "561468123456789", "Karta raqami 16 ta raqamdan iborat bo'lishi kerak."],
      ["digits a card does not carry", "Uzcard", "٨٦٠٠١٢٣٤٥٦٧٨٩٠١٣", "Karta raqami 16 ta raqamdan iborat bo'lishi kerak."],
      ["no name", "  ", "5614681234567890", "Karta nomi 1 dan 40 belgigacha bo'lishi kerak."],
      ["a name of forty-one characters", "x".repeat(41), "5614681234567890", "Karta nomi 1 dan 40 belgigacha bo'lishi kerak."],
      ["a number already in the list", "Yana Humo", "8600 1234 5678 9012", "Bu raqam ro'yxatda allaqachon bor."],
    ])("sends nothing for a card with %s, and says what is wrong under that card", async (_, label, number, message) => {
      const server = open();
      await screen.findByLabelText(DAYS);
      press("Karta qo'shish");
      fill("2-karta nomi", label);
      fill("2-karta raqami", number);
      // Nothing is said while it is being typed.
      expect(document.querySelectorAll(".field__error")).toHaveLength(0);
      // No code is asked for either: a list that cannot be sent is not a change yet.
      expect(screen.queryByLabelText(CODE)).toBeNull();
      save();
      expect(document.getElementById("setting-payment_cards-1-error")?.textContent).toBe(message);
      expect(document.getElementById("setting-payment_cards-0-error")).toBeNull();
      expect(document.getElementById("setting-payment_cards-error")?.textContent).toBe("Kartalardagi xatolarni tuzating.");
      expect(server.writes()).toHaveLength(0);
    });

    it("sends nothing for a card that was added and left empty", async () => {
      const server = open();
      await screen.findByLabelText(DAYS);
      press("Karta qo'shish");
      save();
      expect(document.getElementById("setting-payment_cards-1-error")?.textContent).toBe("Karta raqami 16 ta raqamdan iborat bo'lishi kerak.");
      expect(server.writes()).toHaveLength(0);
    });

    it("offers no eleventh card", async () => {
      const ten = Array.from({ length: 10 }, (_, place) => ({ number: `86001234567890${String(place).padStart(2, "0")}`, label: `Karta ${place + 1}` }));
      open(undefined, withCards(...ten));
      await screen.findByLabelText(DAYS);
      expect(screen.queryByRole("button", { name: "Karta qo'shish" })).toBeNull();
      expect(screen.getByText("Ro'yxat to'la: 10 tadan ortiq karta kiritib bo'lmaydi.")).toBeTruthy();
      press("10-kartani olib tashlash");
      expect(screen.getByRole("button", { name: "Karta qo'shish" })).toBeTruthy();
    });

    it("puts the server's refusal of the cards next to them", async () => {
      const server = open(() => refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { "changes.payment_cards": "card 2: the same number is in the list twice" }));
      await screen.findByLabelText(DAYS);
      press("Karta qo'shish");
      fill("2-karta nomi", "Uzcard");
      fill("2-karta raqami", "5614681234567890");
      fireEvent.change(screen.getByLabelText(CODE), { target: { value: "123456" } });
      save();
      await waitFor(() => expect(server.writes()).toHaveLength(1));
      await waitFor(() => expect(document.getElementById("setting-payment_cards-error")?.textContent).toContain("10 tagacha karta"));
    });

    it("is in Russian when that is the language", async () => {
      const made = adminApi(() => ok(withCards(HUMO, UZCARD)));
      renderAdmin(<SettingsScreen api={made.api} />, "ru");
      const cards = await screen.findByRole("group", { name: "Карты для приёма оплаты" });
      expect(within(cards).getByText("Основная карта")).toBeTruthy();
      expect(within(cards).getByLabelText("Название карты 2")).toHaveProperty("value", "Uzcard · Kapitalbank");
      expect(within(cards).getByRole("button", { name: "Убрать карту 2" })).toBeTruthy();
      expect(within(cards).getByRole("button", { name: "Добавить карту" })).toBeTruthy();
    });
  });

  it("refuses a reason that does not fit, and sends none when none is written", async () => {
    const server = open();
    fireEvent.change(await screen.findByLabelText(DAYS), { target: { value: "14" } });
    fireEvent.change(screen.getByLabelText("Sabab (ixtiyoriy)"), { target: { value: "ab" } });
    save();
    expect(screen.getByText("Sabab 3 dan 500 belgigacha bo'lishi kerak.").id).toBe("settings-reason-error");
    expect(server.writes()).toHaveLength(0);
  });

  it("shows a wrong code next to the code, and a new code is the same request: the key is reused", async () => {
    const message = "Kod noto'g'ri, eskirgan yoki allaqachon ishlatilgan. Yangi kodni kiriting.";
    let wrong = true;
    const server = open((sent) =>
      wrong ? refusal(403, "SECOND_FACTOR_INVALID", message) : ok(platformBody({}, (sent.body as { changes: Record<string, unknown> }).changes)),
    );
    fireEvent.change(await screen.findByLabelText(PRICE), { target: { value: "120000" } });
    fireEvent.change(screen.getByLabelText(CODE), { target: { value: "111111" } });
    save();
    expect((await screen.findByText(message)).id).toBe("settings-code-error");
    expect(screen.queryByRole("status")).toBeNull();
    wrong = false;
    fireEvent.change(screen.getByLabelText(CODE), { target: { value: "222222" } });
    save();
    await screen.findByRole("status");
    expect(server.writes().map((sent) => (sent.body as { code: string }).code)).toEqual(["111111", "222222"]);
    expect(server.writes()[1]?.headers["Idempotency-Key"]).toBe(server.writes()[0]?.headers["Idempotency-Key"]);
  });

  it("puts the server's refusal of a value next to that setting, and of a missing code next to the code", async () => {
    let fields: Record<string, string> = { "changes.trial_days": "must be a whole number between 1 and 365" };
    const server = open(() => refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", fields));
    fireEvent.change(await screen.findByLabelText(DAYS), { target: { value: "14" } });
    // Nothing is said next to a field until something is wrong with it.
    expect(document.querySelectorAll(".field__error")).toHaveLength(0);
    save();
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    await waitFor(() => expect((screen.getByRole("button", { name: "Saqlash" }) as HTMLButtonElement).disabled).toBe(false));
    expect(document.getElementById("setting-trial_days-error")?.textContent).toBe("1 dan 365 gacha butun son.");
    expect(document.getElementById("setting-price_uzs-error")).toBeNull();
    fields = { code: "this change needs a current code from your authenticator" };
    fireEvent.change(screen.getByLabelText(PRICE), { target: { value: "120000" } });
    fireEvent.change(screen.getByLabelText(CODE), { target: { value: "123456" } });
    save();
    await waitFor(() => expect(document.getElementById("settings-code-error")?.textContent).toBe("Kod aynan 6 ta raqamdan iborat."));
    expect(server.writes()).toHaveLength(2);
  });

  it("shows a lock and any other refusal with the server's message", async () => {
    const locked = "Juda ko'p noto'g'ri kod kiritildi. Birozdan keyin qayta urinib ko'ring.";
    open(() => refusal(429, "SECOND_FACTOR_LOCKED", locked, { retry_after_seconds: "600" }));
    fireEvent.change(await screen.findByLabelText(PRICE), { target: { value: "120000" } });
    fireEvent.change(screen.getByLabelText(CODE), { target: { value: "123456" } });
    save();
    expect(await screen.findByText(locked)).toBeTruthy();
    cleanup();
    open(() => "offline");
    fireEvent.change(await screen.findByLabelText(DAYS), { target: { value: "14" } });
    save();
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi");
  });

  it("shows a setting it does not know as the server gave it, without a field to change it", async () => {
    open(undefined, platformBody({}, { referral_bonus: 5 }));
    await screen.findByLabelText(DAYS);
    expect(screen.getByText("referral_bonus").tagName).toBe("DT");
    expect(screen.queryByLabelText("referral_bonus")).toBeNull();
  });
});

describe("the audit", () => {
  const ROWS = [
    auditBody(),
    auditBody({ id: "a2", action: "setting.changed", target_type: "setting", target_id: "price_uzs", shop_id: null, reason: null, detail: { before: 100000, after: 120000 } }),
    auditBody({ id: "a3", action: "admin.session_opened", target_type: "admin", target_id: "x", shop_id: null, reason: null, detail: {} }),
    auditBody({ id: "a4", action: "future.thing", target_type: "gadget", target_id: null, shop_id: null, reason: null, detail: {} }),
  ];
  const open = (shopId: string | null = null, handler: (sent: Sent) => Reply = () => ok({ items: ROWS, next_cursor: null })) => {
    const made = adminApi(handler);
    renderAdmin(<AuditScreen api={made.api} shopId={shopId} />);
    return made.server;
  };
  const asked = (server: ReturnType<typeof adminApi>["server"]) => server.sent.map((sent) => sent.query);

  it("is a table of who did what, about what, why, and what changed", async () => {
    open();
    const table = await screen.findByRole("table", { name: "Audit jurnali" });
    const rows = cells(table);
    expect(rows[0]).toEqual(["Vaqt", "Administrator", "Amal", "Nima haqida", "Sabab", "Tafsilot"]);
    // Beside the administrator's code sits the control that narrows the audit to them.
    const who = "a1b2c3Faqat shu administrator";
    expect(rows[1]?.slice(0, 5)).toEqual(["2026-yil 5-oktabr, 11:00", who, "Sinov muddati belgilandi", "Do'kon · 00aaaa", "Egasi so'radi"]);
    expect(rows[2]).toEqual(["2026-yil 5-oktabr, 11:00", who, "Sozlama o'zgartirildi", "Oylik obuna narxi, so'm", "—", "before: 100000; after: 120000"]);
    expect(rows[3]?.slice(2)).toEqual(["Admin sessiyasi ochildi", "Administrator", "—", "—"]);
    // An action and a target the catalog does not know are shown under the server's own words.
    expect(rows[4]?.slice(2, 4)).toEqual(["future.thing", "gadget"]);
    expect(within(table).getByRole("link", { name: "Do'kon · 00aaaa" }).getAttribute("href")).toBe(`#/shops/${SHOP_ID}`);
    // Nothing in the table changes anything: its only controls are the filters by administrator.
    expect(table.querySelectorAll("input, select, textarea")).toHaveLength(0);
    expect([...table.querySelectorAll("button")].map((button) => button.textContent)).toEqual(ROWS.map(() => "Faqat shu administrator"));
  });

  it("names a review-group administrator by the Telegram identifier and offers no filter for them", async () => {
    // A receipt decided from the review group: the row has no administrator, only a Telegram identifier (DEC-064).
    const fromGroup = auditBody({ id: "g1", admin_id: null, actor_tg_id: 700123456, action: "subscription.receipt_approved", target_type: "receipt", target_id: "r1", reason: null, detail: { via: "group" } });
    open(null, () => ok({ items: [fromGroup, auditBody()], next_cursor: null }));
    const table = await screen.findByRole("table", { name: "Audit jurnali" });
    const rows = cells(table);
    expect(rows[1]?.[1]).toBe("Guruh administratori (Telegram ID 700123456)");
    expect(rows[2]?.[1]).toBe("a1b2c3Faqat shu administrator");
    expect([...table.querySelectorAll("button")].map((button) => button.textContent)).toEqual(["Faqat shu administrator"]);
  });

  it("refuses an audit row whose Telegram identifier is not a positive whole number", async () => {
    for (const wrong of ["700123456", 0, -5, 1.5]) {
      const made = adminApi(() => ok({ items: [auditBody({ admin_id: null, actor_tg_id: wrong })], next_cursor: null }));
      await expect(made.api.listAudit({})).rejects.toMatchObject({ code: "BAD_RESPONSE" });
    }
  });

  it("filters by the kind of action, as the start of its name", async () => {
    const server = open();
    const select = (await screen.findByLabelText("Amal")) as HTMLSelectElement;
    expect([...select.options].map((option) => option.value)).toEqual(["", "admin", "shop", "subscription", "setting", "support"]);
    expect(asked(server)).toEqual([{}]);
    fireEvent.change(select, { target: { value: "subscription" } });
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ action: "subscription" }));
  });

  it("is narrowed to one shop by the address, and changes the address when another shop is typed", async () => {
    const server = open(SHOP_ID);
    await screen.findByRole("table");
    expect(asked(server)).toEqual([{ shop_id: SHOP_ID }]);
    const field = screen.getByLabelText("Do'kon ID (ixtiyoriy)") as HTMLInputElement;
    expect(field.value).toBe(SHOP_ID);
    fireEvent.change(field, { target: { value: OTHER_SHOP.toUpperCase() } });
    fireEvent.click(screen.getByRole("button", { name: "Qo'llash" }));
    expect(window.location.hash).toBe(`#/audit/${OTHER_SHOP}`);
    fireEvent.change(field, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Qo'llash" }));
    expect(window.location.hash).toBe("#/audit");
  });

  it("goes nowhere for something that is not a shop identifier", async () => {
    const server = open();
    fireEvent.change(await screen.findByLabelText("Do'kon ID (ixtiyoriy)"), { target: { value: "baraka" } });
    fireEvent.click(screen.getByRole("button", { name: "Qo'llash" }));
    expect(screen.getByText("Do'kon ID to'liq UUID bo'lishi kerak.").id).toBe("audit-shop-error");
    expect(window.location.hash).toBe("");
    expect(asked(server)).toHaveLength(1);
  });

  it("reads the next page with the cursor, says so when there is nothing, and never writes", async () => {
    const server = open(null, (sent) =>
      sent.query["action"] === "shop"
        ? ok({ items: [], next_cursor: null })
        : sent.query["cursor"] === "c2"
          ? ok({ items: [auditBody({ id: "b2", action: "subscription.suspended" })], next_cursor: null })
          : ok({ items: [auditBody()], next_cursor: "c2" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Yana ko'rsatish" }));
    expect(await screen.findByText("Do'kon to'xtatildi")).toBeTruthy();
    expect(asked(server).at(-1)).toEqual({ cursor: "c2" });
    fireEvent.change(screen.getByLabelText("Amal"), { target: { value: "shop" } });
    expect(await screen.findByText("Bu filtr bo'yicha yozuv yo'q.")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
  });
});
