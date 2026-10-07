// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { catalogs, compareCatalogs, placeholdersOf, translate } from "../../i18n/catalog";
import { ruReceipts } from "../../i18n/receipts/ru";
import { uzReceipts } from "../../i18n/receipts/uz";
import type { MessageKey } from "../../i18n/types";
import { uz } from "../../i18n/uz";
import { deferred, fakeServer, ok, ownReceiptBody, refusal, type Reply, SHOP_BASE, SHOP_ID, subscriptionBody } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import { createApi, RECEIPT_MAX_BYTES } from "../api";
import type { Role } from "../navigation";
import { SubscriptionScreen } from "../workspace/SubscriptionScreen";
import { MAX_AMOUNT, MAX_MONTHS, MAX_WAITING, MIN_AMOUNT, parseMonths, receiptsOf } from "./receiptsApi";
import ReceiptSection, { computedAmount } from "./ReceiptSection";

afterEach(cleanup);

const PATH = `${SHOP_BASE}/subscription/receipts`;
const QUIET = 60;
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** A shop's receipts behind a fake server; `onSend` answers the upload, by default as the API does. */
function shop(items: unknown[] = [], onSend: () => Reply = () => ok(ownReceiptBody(), 201)) {
  const held = { items, list: null as Reply | null };
  const server = fakeServer((sent) => {
    if (sent.path.endsWith("/subscription")) {
      return ok(subscriptionBody());
    }
    if (sent.method === "GET") {
      return held.list ?? ok({ items: held.items });
    }
    return onSend();
  });
  return { ...server, held };
}
type Shop = ReturnType<typeof shop>;
const lists = (server: Shop) => server.sent.filter((sent) => sent.method === "GET" && sent.path === PATH);
const show = (server: Shop, language: "uz" | "ru" = "uz") => renderScreen(<ReceiptSection priceUzs={100000} />, { fetch: server.fetch, role: "owner", language });
const months = () => screen.getByLabelText("Necha oy uchun to'ladingiz") as HTMLInputElement;
const amount = () => screen.getByLabelText("O'tkazilgan summa") as HTMLInputElement;
const fileField = () => screen.getByLabelText("Chek") as HTMLInputElement;
const type = (field: HTMLElement, value: string) => fireEvent.change(field, { target: { value } });
const image = (name = "chek.jpg", mime = "image/jpeg", content: BlobPart[] = ["jpeg"]) => new File(content, name, { type: mime });
const choose = (file: File) => fireEvent.change(fileField(), { target: { files: [file] } });
const send = () => fireEvent.submit(screen.getByRole("form", { name: "Chek yuborish" }));
const said = (field: HTMLElement) => field.parentElement?.querySelector("[role=alert]")?.textContent?.replace(/\s/g, " ");
const history = (name = "Yuborilgan cheklar") =>
  within(screen.getByRole("list", { name }))
    .getAllByRole("listitem")
    .map((row) => [...row.querySelectorAll("p")].map((part) => part.textContent?.replace(/\s/g, " ")));

describe("the owner's receipt API", () => {
  const api = (server: ReturnType<typeof fakeServer>) => receiptsOf(createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "t" } }).shop(SHOP_ID));

  it("sends a multipart form with amount, months and the file, with a key; and reads the history", async () => {
    const server = shop([ownReceiptBody({ status: "approved", months: 3, decided_at: "2026-10-06T06:30:00+00:00" })]);
    const file = image();
    expect(await api(server).submit({ amount: 200000, months: 2, receipt: file }, "key-0000-0001")).toMatchObject({ status: "submitted", statedAmount: 200000, statedMonths: 2 });
    expect(await api(server).list()).toEqual([
      {
        id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
        statedAmount: 200000,
        statedMonths: 2,
        status: "approved",
        months: 3,
        rejectReason: null,
        createdAt: "2026-10-06T06:00:00+00:00",
        decidedAt: "2026-10-06T06:30:00+00:00",
      },
    ]);
    const [posted, listed] = server.sent;
    expect([posted?.method, posted?.path, posted?.body, posted?.headers["Idempotency-Key"]]).toEqual(["POST", PATH, undefined, "key-0000-0001"]);
    expect(Object.keys(posted?.form ?? {}).sort()).toEqual(["amount", "months", "receipt"]);
    expect([posted?.form?.["amount"], posted?.form?.["months"]]).toEqual(["200000", "2"]);
    expect((posted?.form?.["receipt"] as File).name).toBe("chek.jpg");
    // The browser writes the content type with its boundary; none is set by hand.
    expect(posted?.headers["Content-Type"]).toBeUndefined();
    expect([listed?.method, listed?.path]).toEqual(["GET", PATH]);
  });

  it("sends no amount or months that is not a whole number, and refuses answers that are not the contract", async () => {
    const server = shop();
    expect(() => api(server).submit({ amount: 1.5, months: 1, receipt: image() }, "key-0000-0001")).toThrow(RangeError);
    expect(() => api(server).submit({ amount: 1000, months: Number.NaN, receipt: image() }, "key-0000-0001")).toThrow(RangeError);
    expect(server.sent).toEqual([]);
    for (const wrong of [{ stated_amount: 1.5 }, { id: 7 }, { status: null }, { months: "2" }]) {
      await expect(api(fakeServer(() => ok({ items: [ownReceiptBody(wrong)] }))).list()).rejects.toMatchObject({ code: "BAD_RESPONSE" });
    }
  });

  it("repeats the server's bounds, and reads months as plain digits from 1 to 36", () => {
    expect([MAX_MONTHS, MIN_AMOUNT, MAX_AMOUNT, MAX_WAITING]).toEqual([36, 1000, 360_000_000, 3]);
    expect(["1", " 12 ", "36", "0", "37", "1.5", "-1", "2e1", "", "abc", "012"].map(parseMonths)).toEqual([1, 12, 36, null, null, null, null, null, null, null, null]);
    expect([computedAmount(100000, "3"), computedAmount(100000, "0"), computedAmount(100000, "x")]).toEqual(["300000", "", ""]);
  });
});

describe("who is offered to send a receipt", () => {
  it("shows the owner the section under the card, and sends nothing by opening the screen", async () => {
    const server = shop([ownReceiptBody()]);
    renderScreen(<SubscriptionScreen />, { fetch: server.fetch, role: "owner" });
    expect(await screen.findByRole("form", { name: "Chek yuborish" })).toBeTruthy();
    await waitFor(() => expect(history()).toHaveLength(1));
    expect(server.sent.map((sent) => `${sent.method} ${sent.path}`)).toEqual([`GET ${SHOP_BASE}/subscription`, `GET ${PATH}`]);
  });

  it.each(["manager", "seller"] as Role[])("gives a %s the not-found screen: no form, and nothing asked", async (role) => {
    const server = shop([ownReceiptBody()]);
    renderScreen(<SubscriptionScreen />, { fetch: server.fetch, role });
    expect(screen.getByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    await pause(QUIET);
    expect(screen.queryByRole("form")).toBeNull();
    expect(server.sent).toEqual([]);
  });
});

describe("the form", () => {
  it("starts with one month and the price, and follows the months until the amount is typed by hand", async () => {
    show(shop());
    expect([months().value, amount().value]).toEqual(["1", "100000"]);
    type(months(), "3");
    expect(amount().value).toBe("300000");
    expect(screen.getByText(/Narx bo'yicha hisoblangan: 3 oy uchun 300\s000 so'm\./)).toBeTruthy();
    type(months(), "40");
    expect(amount().value).toBe("");
    type(months(), "2");
    type(amount(), "150000");
    type(months(), "5");
    expect(amount().value).toBe("150000");
  });

  it("sends nothing while the months, the amount or the file do not fit, and says which next to each", async () => {
    const server = shop();
    show(server);
    send();
    expect(said(fileField())).toBe("Chek faylini tanlang.");
    for (const typed of ["0", "37", "1.5", ""]) {
      type(months(), typed);
      type(amount(), "100000");
      send();
      expect(said(months())).toBe("Oylar soni 1 dan 36 gacha butun son bo'lishi kerak.");
    }
    type(months(), "1");
    expect(said(months())).toBeUndefined();
    for (const typed of ["999", "360000001", "12,5", "", "abc"]) {
      type(amount(), typed);
      send();
      expect(said(amount())).toBe("Summa 1 000 so'm dan 360 000 000 so'm gacha, butun so'mda bo'lishi kerak.");
    }
    type(amount(), "100000");
    for (const [file, text] of [
      [image("chek.txt", "text/plain"), "Faqat JPEG, PNG, WebP rasm yoki PDF yuboriladi."],
      [image("bosh.png", "image/png", []), "Fayl bo'sh."],
      [image("katta.pdf", "application/pdf", [new Uint8Array(RECEIPT_MAX_BYTES + 1)]), "Fayl 5 MB dan katta."],
    ] as const) {
      choose(file);
      send();
      expect(said(fileField())).toBe(text);
    }
    await pause(QUIET);
    expect(server.writes()).toEqual([]);
  });

  it("sends one request with a key however often it is submitted, then says so, empties the form and reads the history again", async () => {
    const gate = deferred<Exclude<Reply, Promise<unknown>>>();
    const server = shop([], () => gate.promise);
    show(server);
    await screen.findByText("Hali chek yuborilmagan.");
    type(months(), "2");
    const file = image("chek.pdf", "application/pdf");
    choose(file);
    send();
    send();
    await waitFor(() => expect((screen.getByRole("button", { name: "Saqlanmoqda…" }) as HTMLButtonElement).disabled).toBe(true));
    send();
    server.held.items = [ownReceiptBody()];
    gate.resolve(ok(ownReceiptBody(), 201));
    expect(await screen.findByText(/^Chek yuborildi\./)).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
    const posted = server.writes()[0];
    expect([posted?.method, posted?.path, posted?.form?.["amount"], posted?.form?.["months"]]).toEqual(["POST", PATH, "200000", "2"]);
    expect(posted?.form?.["receipt"]).toBe(file);
    expect(posted?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    await waitFor(() => expect(history()).toEqual([["2026-yil 6-oktabr, 11:00200 000 so'm", "2 oy uchun", "Ko'rib chiqilishini kutmoqda"]]));
    expect([months().value, amount().value, fileField().files?.length ?? 0]).toEqual(["1", "100000", 0]);
    expect(lists(server)).toHaveLength(2);
  });

  it("sends the amount the person typed, not the computed one", async () => {
    const server = shop();
    show(server);
    type(months(), "3");
    type(amount(), "250 000");
    choose(image());
    send();
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect([server.writes()[0]?.form?.["amount"], server.writes()[0]?.form?.["months"]]).toEqual(["250000", "3"]);
  });

  it.each([
    [refusal(422, "VALIDATION", "Ma'lumot noto'g'ri.", { months: "a whole number between 1 and 36" }), "months", "Oylar soni 1 dan 36 gacha butun son bo'lishi kerak."],
    [refusal(422, "VALIDATION", "Ma'lumot noto'g'ri.", { amount: "a whole amount" }), "amount", "Summa 1 000 so'm dan 360 000 000 so'm gacha, butun so'mda bo'lishi kerak."],
    [refusal(422, "VALIDATION", "Ma'lumot noto'g'ri.", { receipt: "malformed" }), "file", "Fayl buzilgan yoki to'liq emas. Boshqa nusxasini yuboring."],
    [refusal(422, "VALIDATION", "Ma'lumot noto'g'ri.", { receipt: "type" }), "file", "Faqat JPEG, PNG, WebP rasm yoki PDF yuboriladi."],
    [refusal(422, "VALIDATION", "Ma'lumot noto'g'ri.", { receipt: "required" }), "file", "Chek faylini tanlang."],
    [refusal(422, "VALIDATION", "Ma'lumot noto'g'ri.", { receipt: "something_new" }), "file", "Ma'lumot noto'g'ri."],
    [refusal(413, "BODY_TOO_LARGE", "So'rov juda katta."), "file", "Fayl 5 MB dan katta."],
  ] as const)("puts the server's refusal next to the field it is about (%#)", async (answer, where, text) => {
    show(shop([], () => answer));
    choose(image());
    send();
    const field = { months, amount, file: fileField }[where];
    await waitFor(() => expect(said(field())).toBe(text));
    expect(field().getAttribute("aria-invalid")).toBe("true");
  });

  it.each([
    [refusal(409, "SUBSCRIPTION_RECEIPT_NOT_ALLOWED", "Ko'rib chiqilmagan cheklaringiz juda ko'p.", { reason: "too_many_waiting" }), "Ko'rib chiqilmagan cheklaringiz 3 ta: bundan ortiq yuborib bo'lmaydi. Administrator javobini kuting."],
    [refusal(409, "SUBSCRIPTION_RECEIPT_NOT_ALLOWED", "Ko'rib chiqilmagan cheklaringiz juda ko'p.", { reason: "new_rule" }), "Ko'rib chiqilmagan cheklaringiz juda ko'p."],
    [refusal(503, "FILE_STORE_UNAVAILABLE", "Fayl ombori ishlamayapti."), "Chekni hozir saqlab bo'lmadi. Birozdan so'ng qayta urinib ko'ring."],
    [refusal(404, "NOT_FOUND", "Topilmadi."), "Topilmadi."],
  ])("says a refusal about no field above the form (%#), and resends the same key when sent again", async (answer, text) => {
    const server = shop([], () => answer);
    show(server);
    choose(image());
    send();
    expect((await within(screen.getByRole("form", { name: "Chek yuborish" })).findByRole("alert")).textContent).toBe(text);
    send();
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toBe(server.writes()[1]?.headers["Idempotency-Key"]);
    expect(screen.queryByText(/^Chek yuborildi\./)).toBeNull();
  });
});

describe("the history of receipts", () => {
  it("says what became of each: waiting, approved with the months counted, rejected with the reason", async () => {
    show(
      shop([
        ownReceiptBody(),
        ownReceiptBody({ id: "r2", status: "approved", months: 3, stated_months: 2 }),
        ownReceiptBody({ id: "r3", status: "rejected", reject_reason: "Summa mos kelmadi", stated_amount: 50000, stated_months: 1 }),
        ownReceiptBody({ id: "r4", status: "approved", months: null, stated_amount: null, stated_months: null }),
        ownReceiptBody({ id: "r5", status: "rejected" }),
        ownReceiptBody({ id: "r6", status: "on_hold" }),
      ]),
    );
    await waitFor(() =>
      expect(history()).toEqual([
        ["2026-yil 6-oktabr, 11:00200 000 so'm", "2 oy uchun", "Ko'rib chiqilishini kutmoqda"],
        ["2026-yil 6-oktabr, 11:00200 000 so'm", "2 oy uchun", "Tasdiqlandi. Hisobga olingan oylar: 3"],
        ["2026-yil 6-oktabr, 11:0050 000 so'm", "1 oy uchun", "Rad etildi. Sabab: Summa mos kelmadi"],
        ["2026-yil 6-oktabr, 11:00—", "Tasdiqlandi"],
        ["2026-yil 6-oktabr, 11:00200 000 so'm", "2 oy uchun", "Rad etildi"],
        ["2026-yil 6-oktabr, 11:00200 000 so'm", "2 oy uchun", "on_hold"],
      ]),
    );
  });

  it("says so when there is none, and shows the server's refusal with a retry when it cannot be read", async () => {
    const server = shop();
    server.held.list = refusal(500, "ERROR", "Xatolik yuz berdi.");
    show(server);
    expect(await screen.findByText("Xatolik yuz berdi.")).toBeTruthy();
    server.held.list = null;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByText("Hali chek yuborilmagan.")).toBeTruthy();
  });

  it("says the same in Russian", async () => {
    show(shop([ownReceiptBody({ status: "approved", months: 2 })]), "ru");
    expect(screen.getByRole("form", { name: "Отправить чек" })).toBeTruthy();
    await waitFor(() => expect(history("Отправленные чеки")).toEqual([["6 октября 2026 г., 11:00200 000 сум", "за 2 месяца", "Подтверждён. Засчитано месяцев: 2"]]));
  });
});

describe("the receipts' catalog", () => {
  it("has the same keys, kinds and placeholders in both languages, and shares none with the main catalog", () => {
    expect(compareCatalogs(uzReceipts, ruReceipts)).toEqual({ missingInSecond: [], missingInFirst: [], kindMismatch: [], placeholderMismatch: [] });
    expect(Object.keys(uzReceipts).filter((key) => key in uz)).toEqual([]);
    const without = Object.fromEntries(Object.entries(ruReceipts).filter(([key]) => key !== "receipts.send"));
    expect(compareCatalogs(uzReceipts, without).missingInSecond).toEqual(["receipts.send"]);
  });

  it("resolves every message in both languages, in plain Uzbek letters", () => {
    for (const lang of ["uz", "ru"] as const) {
      for (const key of Object.keys(uzReceipts) as MessageKey[]) {
        const message = catalogs[lang][key];
        const template = typeof message === "string" ? message : (Object.values(message)[0] ?? "");
        const params = Object.fromEntries(placeholdersOf(template).map((name) => [name, 3]));
        expect(translate(lang, key, typeof message === "string" ? params : { ...params, count: 3 })).not.toMatch(/[{}]/);
      }
    }
    const entries = Object.entries(uzReceipts);
    expect(entries.filter(([, message]) => /[`‘’ʻʼ´]|\p{Script=Cyrillic}/u.test(JSON.stringify(message))).map(([key]) => key)).toEqual([]);
  });
});
