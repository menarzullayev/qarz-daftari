// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { I18nProvider } from "../../i18n/I18nProvider";
import type { Language } from "../../i18n/types";
import {
  accountBody,
  accountEntryBody,
  dateRequestBody,
  deferred,
  fakeServer,
  LINK_ID,
  ME_BASE,
  NOON,
  ok,
  promiseBody,
  refusal,
  type Reply,
  type Sent,
} from "../../testing/fakeServer";
import { type AccountEntry, createApi } from "../api";
import { AccountScreen, laterDate } from "./AccountScreen";

afterEach(cleanup);

const CREDIT_ID = "22222222-2222-4222-8222-222222222222";
// Sold on 6 October 2026 (Tashkent), promised for 5 November: a later day is 6 November to 6 October 2027.
const CREDIT = accountEntryBody({ id: CREDIT_ID, amount: 140000 });
const PAYMENT = accountEntryBody({
  id: "66666666-6666-4666-8666-666666666666",
  kind: "payment",
  amount: 20000,
  created_at: "2026-10-06T06:05:00+00:00",
  promised_date: null,
});
const ASK = "Muddatni kechroq so'rash";
const LABEL = "Qaysi kungacha to'laysiz?";

/** The customer's account with the given entries; `onWrite` answers the request for a later date. */
function backend(entries: unknown[] = [PAYMENT, CREDIT], onWrite: (sent: Sent) => Reply = () => ok(dateRequestBody(), 201), balance = 120000) {
  const held = { entries };
  const server = fakeServer((sent) => (sent.method === "GET" ? ok(accountBody({ entries: held.entries, entries_total: held.entries.length, balance })) : onWrite(sent)));
  return { ...server, held };
}

async function open(server: ReturnType<typeof fakeServer>, language: Language = "uz", now: Date = NOON) {
  const api = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "customer-session" } }).account(LINK_ID);
  const view = render(
    <I18nProvider initialLanguage={language}>
      <AccountScreen api={api} back={<a href="#/my">back</a>} now={() => now} />
    </I18nProvider>,
  );
  await screen.findByRole("heading", { level: 2, name: "Baraka savdo" });
  return view;
}

const rows = () =>
  within(screen.getByRole("region", { name: /Yozuvlar|Записи/ }))
    .getAllByRole("listitem")
    .filter((item) => item.classList.contains("row"));
const creditRow = () => rows().find((row) => within(row).queryByText("Nasiya") !== null) as HTMLElement;
const askButtons = () => screen.queryAllByRole("button", { name: ASK });

async function fill(date: string, reason = "") {
  fireEvent.click(screen.getByRole("button", { name: ASK }));
  fireEvent.change(screen.getByLabelText(LABEL), { target: { value: date } });
  if (reason !== "") {
    fireEvent.change(screen.getByLabelText("Sabab (ixtiyoriy)"), { target: { value: reason } });
  }
  fireEvent.click(screen.getByRole("button", { name: "So'rovni yuborish" }));
}

describe("which entries are offered a later date", () => {
  const entry = (overrides: Partial<AccountEntry> = {}): AccountEntry => ({
    id: CREDIT_ID,
    kind: "credit",
    amount: 140000,
    createdAt: "2026-10-05T19:30:00+00:00",
    promisedDate: "2026-11-05",
    reversesId: null,
    reversed: false,
    disputed: false,
    dispute: null,
    lines: [],
    promises: [],
    dateRequest: null,
    ...overrides,
  });
  const request = (status: string, closedAt: string | null = null) => ({
    id: "r",
    entryId: CREDIT_ID,
    status,
    requestedDate: "2026-11-20",
    reason: null,
    declineReason: null,
    createdAt: "2026-10-01T05:00:00+00:00",
    closedAt,
  });

  it("offers it for a debt that stands on an account that still owes, with the days to choose from", () => {
    expect(laterDate(entry(), 120000, NOON)).toEqual({
      sale: { year: 2026, month: 10, day: 6 },
      current: { year: 2026, month: 11, day: 5 },
      range: { min: { year: 2026, month: 11, day: 6 }, max: { year: 2027, month: 10, day: 6 } },
    });
    expect(laterDate(entry({ kind: "opening" }), 1, NOON)).not.toBeNull();
  });

  it.each([
    ["a payment", entry({ kind: "payment", promisedDate: null }), 120000],
    ["a reversal", entry({ kind: "reversal" }), 120000],
    ["a reversed entry", entry({ reversed: true }), 120000],
    ["an account that owes nothing", entry(), 0],
    ["an entry with no promised date", entry({ promisedDate: null }), 120000],
    ["an entry with a request waiting", entry({ dateRequest: request("open") }), 120000],
    ["an entry declined two days ago", entry({ dateRequest: request("declined", "2026-10-04T07:00:00+00:00") }), 120000],
    ["an entry whose date is already the last day there is", entry({ promisedDate: "2027-10-06" }), 120000],
    ["an entry whose time cannot be read", entry({ createdAt: "?" }), 120000],
  ])("does not offer it for %s", (_name, value, balance) => {
    expect(laterDate(value, balance, NOON)).toBeNull();
  });

  it("offers it again once seven days have passed since a decline, and after any other answer", () => {
    expect(laterDate(entry({ dateRequest: request("declined", "2026-09-29T07:00:00+00:00") }), 120000, NOON)).not.toBeNull();
    expect(laterDate(entry({ dateRequest: request("declined", "2026-09-29T07:00:01+00:00") }), 120000, NOON)).toBeNull();
    expect(laterDate(entry({ dateRequest: request("accepted", "2026-10-05T07:00:00+00:00") }), 120000, NOON)).not.toBeNull();
    expect(laterDate(entry({ dateRequest: request("expired", "2026-10-05T07:00:00+00:00") }), 120000, NOON)).not.toBeNull();
  });

  it("shows the button on the credit entry only", async () => {
    await open(backend());
    expect(askButtons()).toHaveLength(1);
    expect(within(creditRow()).getByRole("button", { name: ASK })).toBeTruthy();
  });

  it("shows no button when nothing is owed", async () => {
    await open(backend([PAYMENT, CREDIT], undefined, 0));
    expect(askButtons()).toHaveLength(0);
  });
});

describe("asking for a later date", () => {
  it("opens a date field with the allowed range and an optional reason, and sends nothing yet", async () => {
    const server = backend();
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: ASK }));
    const date = screen.getByLabelText(LABEL) as HTMLInputElement;
    expect([date.type, date.min, date.max]).toEqual(["date", "2026-11-06", "2027-10-06"]);
    expect(screen.getByText("2026-yil 6-noyabr dan 2027-yil 6-oktabr gacha bo'lgan kunni tanlash mumkin.")).toBeTruthy();
    expect(date.getAttribute("aria-describedby")).toContain(`later-${CREDIT_ID}-range`);
    expect((screen.getByLabelText("Sabab (ixtiyoriy)") as HTMLTextAreaElement).required).toBe(false);
    expect(screen.getByText(/javob kelguncha muddat o'zgarmaydi/)).toBeTruthy();
    expect(server.writes()).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Bekor qilish" }));
    expect(screen.queryByLabelText(LABEL)).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });

  it.each([
    ["", "Sanani tanlang."],
    ["2026-11-05", "Yangi sana hozirgi muddatdan keyin bo'lishi kerak."],
    ["2026-10-01", "Sana savdo kunidan oldin bo'lishi mumkin emas."],
    ["2027-10-07", "Sana savdo kunidan ko'pi bilan 365 kun keyin bo'lishi mumkin."],
  ])("sends nothing for the date %j and says why next to the field", async (date, message) => {
    const server = backend();
    await open(server);
    await fill(date);
    const field = screen.getByLabelText(LABEL);
    expect(screen.getByText(message).id).toBe(`later-${CREDIT_ID}-date-error`);
    expect(field.getAttribute("aria-invalid")).toBe("true");
    expect(server.writes()).toHaveLength(0);
  });

  it("sends nothing while the reason is longer than 300 characters", async () => {
    const server = backend();
    await open(server);
    await fill("2026-11-20", "a".repeat(301));
    expect(screen.getByText("Sabab 300 belgidan oshmasligi kerak.")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
  });

  it("sends the entry, the date and the tidied reason once, then shows the request as waiting", async () => {
    const server = backend();
    await open(server);
    server.held.entries = [PAYMENT, accountEntryBody({ id: CREDIT_ID, amount: 140000, date_request: dateRequestBody() })];
    await fill("2026-11-20", "  Oylik \n kechikdi ");
    expect(await screen.findByText("Muddatni 2026-yil 20-noyabr ga ko'chirish so'ralgan. Do'kon javobi kutilmoqda.")).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({
      method: "POST",
      path: `${ME_BASE}/date-requests`,
      body: { entry_id: CREDIT_ID, requested_date: "2026-11-20", reason: "Oylik kechikdi" },
    });
    expect(screen.getByText("Sizning sababingiz: Oylik kechikdi")).toBeTruthy();
    // One request per entry: while it waits there is nothing more to ask.
    expect(askButtons()).toHaveLength(0);
    expect(screen.queryByLabelText(LABEL)).toBeNull();
  });

  it("leaves the reason out when none was written", async () => {
    const server = backend();
    await open(server);
    await fill("2027-10-06", "   ");
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ entry_id: CREDIT_ID, requested_date: "2027-10-06" });
  });

  it("disables the form while the request is in flight, so a double tap sends one", async () => {
    const gate = deferred<ReturnType<typeof ok>>();
    const server = backend([PAYMENT, CREDIT], () => gate.promise);
    await open(server);
    await fill("2026-11-20");
    const button = screen.getByRole("button", { name: "Saqlanmoqda…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    fireEvent.submit(button.closest("form") as HTMLFormElement);
    gate.resolve(ok(dateRequestBody(), 201));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Saqlanmoqda…" })).toBeNull());
    expect(server.writes()).toHaveLength(1);
  });
});

describe("the server's refusals, in the customer's words", () => {
  const GENERAL = "Bu yozuv muddatini ko'chirish so'rovini hozir qabul qilib bo'lmaydi.";
  it.each([
    ["not_a_debt", "Faqat nasiya yozuvining muddatini ko'chirish so'raladi."],
    ["reversed", "Do'kon bu yozuvni bekor qilgan."],
    ["fully_paid", "Bu yozuv to'liq to'langan: ko'chiradigan muddat qolmagan."],
    ["not_later", "Yangi sana hozirgi muddatdan keyin bo'lishi kerak."],
    ["declined_recently", "Do'kon yaqinda rad etgan. Rad javobidan 7 kun o'tgach qayta so'rash mumkin."],
  ])("explains DATE_REQUEST_NOT_ALLOWED: %s", async (reason, detail) => {
    const server = backend([PAYMENT, CREDIT], () => refusal(409, "DATE_REQUEST_NOT_ALLOWED", GENERAL, { reason }));
    await open(server);
    await fill("2026-11-20");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(GENERAL);
    expect(alert.textContent).toContain(detail);
    // The form stays, with what was typed.
    expect((screen.getByLabelText(LABEL) as HTMLInputElement).value).toBe("2026-11-20");
  });

  it("explains a request that is already open", async () => {
    const message = "Bu yozuv bo'yicha muddatni ko'chirish so'rovi allaqachon ko'rib chiqilmoqda.";
    const server = backend([PAYMENT, CREDIT], () => refusal(409, "REQUEST_ALREADY_OPEN", message));
    await open(server);
    await fill("2026-11-20");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(message);
    expect(alert.textContent).toContain("Bu yozuv bo'yicha so'rovingiz allaqachon ko'rib chiqilmoqda.");
  });

  it("shows a refusal with a reason this client does not know as the server worded it", async () => {
    const server = backend([PAYMENT, CREDIT], () => refusal(409, "DATE_REQUEST_NOT_ALLOWED", GENERAL, { reason: "something_new" }));
    await open(server);
    await fill("2026-11-20");
    expect((await screen.findByRole("alert")).textContent).toBe(`! ${GENERAL}`.slice(2));
  });

  it.each([
    ["PROMISE_TOO_FAR", "Sana savdo kunidan ko'pi bilan 365 kun keyin bo'lishi mumkin."],
    ["PROMISE_BEFORE_SALE", "Sana savdo kunidan oldin bo'lishi mumkin emas."],
  ])("puts the server's %s next to the date", async (code, message) => {
    const server = backend([PAYMENT, CREDIT], () =>
      refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { requested_date: code }),
    );
    await open(server);
    await fill("2026-11-20");
    expect((await screen.findByText(message)).id).toBe(`later-${CREDIT_ID}-date-error`);
    expect(screen.getByLabelText(LABEL).getAttribute("aria-invalid")).toBe("true");
  });

  it("puts the server's refusal of the reason next to the reason", async () => {
    const server = backend([PAYMENT, CREDIT], () =>
      refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { reason: "at most 300 characters" }),
    );
    await open(server);
    await fill("2026-11-20", "uzun");
    expect((await screen.findByText("Sabab 300 belgidan oshmasligi kerak.")).id).toBe(`later-${CREDIT_ID}-reason-error`);
  });

  it("shows any other failure with the server's message", async () => {
    const server = backend([PAYMENT, CREDIT], () => refusal(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan."));
    await open(server);
    await fill("2026-11-20");
    expect((await screen.findByRole("alert")).textContent).toContain("Do'kon to'xtatilgan.");
  });
});

describe("the state of the entry's latest request", () => {
  const withRequest = (request: Record<string, unknown>) => [PAYMENT, accountEntryBody({ id: CREDIT_ID, amount: 140000, date_request: dateRequestBody(request) })];

  it("says a request waits for the shop, and offers no second one", async () => {
    await open(backend(withRequest({})));
    expect(within(creditRow()).getByText("Muddatni 2026-yil 20-noyabr ga ko'chirish so'ralgan. Do'kon javobi kutilmoqda.")).toBeTruthy();
    expect(askButtons()).toHaveLength(0);
  });

  it("says the shop agreed, and a still later date may be asked for", async () => {
    await open(backend(withRequest({ status: "accepted", closed_at: "2026-10-06T06:00:00+00:00" })));
    expect(screen.getByText("Do'kon so'rovga rozi bo'ldi: muddat 2026-yil 20-noyabr ga ko'chirildi.")).toBeTruthy();
    expect(askButtons()).toHaveLength(1);
  });

  it("says the shop declined, with its reason, and when the entry may be asked about again", async () => {
    await open(backend(withRequest({ status: "declined", decline_reason: "Muddat juda uzoq", closed_at: "2026-10-04T07:00:00+00:00" })));
    const row = creditRow();
    expect(within(row).getByText("Do'kon muddatni 2026-yil 20-noyabr ga ko'chirish so'rovini rad etdi.")).toBeTruthy();
    expect(within(row).getByText("Do'kon sababi: Muddat juda uzoq")).toBeTruthy();
    expect(within(row).getByText("Bu yozuv bo'yicha qayta so'rash 2026-yil 11-oktabr, 12:00 dan keyin mumkin.")).toBeTruthy();
    expect(askButtons()).toHaveLength(0);
  });

  it("offers the request again once the seven days are over", async () => {
    await open(backend(withRequest({ status: "declined", decline_reason: null, closed_at: "2026-09-20T07:00:00+00:00" })));
    expect(screen.getByText(/so'rovini rad etdi/)).toBeTruthy();
    expect(screen.queryByText(/qayta so'rash/)).toBeNull();
    expect(screen.queryByText(/Do'kon sababi/)).toBeNull();
    expect(askButtons()).toHaveLength(1);
  });

  it("says a request lost its force when the entry was settled or the shop set another date", async () => {
    await open(backend(withRequest({ status: "expired", closed_at: "2026-10-06T06:00:00+00:00" })));
    expect(screen.getByText(/Muddatni 2026-yil 20-noyabr ga ko'chirish so'rovi o'z kuchini yo'qotdi/)).toBeTruthy();
    expect(askButtons()).toHaveLength(1);
  });

  it("says only that a request is closed for a state this client does not know", async () => {
    await open(backend(withRequest({ status: "archived", closed_at: "2026-10-06T06:00:00+00:00" })));
    expect(screen.getByText("Muddatni ko'chirish so'rovi yopilgan.")).toBeTruthy();
  });

  it("reads the same in Russian", async () => {
    await open(backend(withRequest({ status: "declined", decline_reason: "Слишком долго", closed_at: "2026-10-04T07:00:00+00:00" })), "ru");
    expect(screen.getByText("Просьбу перенести срок на 20 ноября 2026 г. магазин отклонил.")).toBeTruthy();
    expect(screen.getByText("Причина магазина: Слишком долго")).toBeTruthy();
  });
});

describe("the promise history of an entry", () => {
  const HISTORY = [
    promiseBody(),
    promiseBody({ promised_date: "2026-11-20", actor: "customer_request", reason: "Oylik kechikdi", created_at: "2026-10-07T05:00:00+00:00" }),
    promiseBody({ promised_date: "2026-11-15", actor: "staff", reason: null, created_at: "2026-10-08T05:00:00+00:00" }),
  ];

  it("lists every date the entry has carried, oldest first, with where each came from", async () => {
    await open(backend([accountEntryBody({ id: CREDIT_ID, promised_date: "2026-11-15", promises: HISTORY })]));
    const items = within(within(creditRow()).getByRole("list")).getAllByRole("listitem");
    expect(items.map((item) => [...item.children].map((part) => part.textContent))).toEqual([
      ["2026-yil 5-noyabr", "do'konning odatdagi muddati, 2026-yil 6-oktabr, 00:30"],
      ["2026-yil 20-noyabr", "sizning so'rovingiz bo'yicha, 2026-yil 7-oktabr, 10:00", "Sabab: Oylik kechikdi"],
      ["2026-yil 15-noyabr (hozirgi muddat)", "do'kon belgilagan, 2026-yil 8-oktabr, 10:00"],
    ]);
    expect(within(creditRow()).getByText("Muddat tarixi")).toBeTruthy();
  });

  it("is not drawn for an entry whose date never changed", async () => {
    await open(backend([accountEntryBody({ id: CREDIT_ID, promises: [promiseBody()] })]));
    expect(screen.queryByText("Muddat tarixi")).toBeNull();
    expect(within(creditRow()).queryByRole("list")).toBeNull();
  });
});
