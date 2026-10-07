// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  creditSettingsBody,
  CUSTOMER_ID,
  customerBody,
  dateRequestBody,
  deferred,
  detailBody,
  entryBody,
  fakeServer,
  linkBody,
  ok,
  promiseBody,
  refusal,
  type Reply,
  type Sent,
  SHOP_BASE,
} from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import type { Entry } from "../api";
import type { Role } from "../navigation";
import { canChangePromise, CustomerScreen } from "./CustomerScreen";

afterEach(cleanup);

const CREDIT_ID = "22222222-2222-4222-8222-222222222222";
// Sold on 6 October 2026 (Tashkent), promised for 5 November: the shop may set 6 October to 6 October 2027.
const CREDIT = entryBody({ id: CREDIT_ID, seq: 1, amount: 140000 });
const PAYMENT = entryBody({
  id: "66666666-6666-4666-8666-666666666666",
  seq: 2,
  kind: "payment",
  amount: 20000,
  created_at: "2026-10-06T06:05:00+00:00",
  promised_date: null,
});
const CHANGE = "Muddatni o'zgartirish";
const LABEL = "Yangi to'lash muddati";

const changed = (date: string, request: unknown = null) => ({
  entry: { id: CREDIT_ID, amount: 140000, promised_date: date, previous_date: "2026-11-05" },
  customer: customerBody(),
  date_request: request,
});

function shop(entries: unknown[] = [PAYMENT, CREDIT], onWrite: (sent: Sent) => Reply = () => ok(changed("2026-11-25"))) {
  const held = { entries };
  const server = fakeServer((sent) => {
    if (sent.method !== "GET") {
      return onWrite(sent);
    }
    if (sent.path.endsWith("/credit-settings")) {
      return ok(creditSettingsBody());
    }
    return sent.path.endsWith("/link") ? ok(linkBody()) : ok(detailBody({ entries: held.entries, entries_total: held.entries.length }));
  });
  return { ...server, held };
}

async function open(server: ReturnType<typeof fakeServer>, role: Role = "manager") {
  renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: server.fetch, role });
  await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
}

const creditRow = () =>
  within(screen.getByRole("region", { name: "Yozuvlar" }))
    .getAllByRole("listitem")
    .find((row) => row.classList.contains("row") && within(row).queryByText("Nasiya") !== null) as HTMLElement;

function fill(date: string, reason = "") {
  fireEvent.click(screen.getByRole("button", { name: CHANGE }));
  fireEvent.change(screen.getByLabelText(LABEL), { target: { value: date } });
  if (reason !== "") {
    fireEvent.change(screen.getByLabelText("Sabab (ixtiyoriy)"), { target: { value: reason } });
  }
  fireEvent.click(screen.getByRole("button", { name: "Muddatni saqlash" }));
}

describe("who may change a promised date, and of which entry", () => {
  const entry = (overrides: Partial<Entry> = {}): Entry => ({
    id: CREDIT_ID,
    seq: 1,
    kind: "credit",
    amount: 140000,
    note: null,
    createdAt: "2026-10-05T19:30:00+00:00",
    promisedDate: "2026-11-05",
    reversesId: null,
    reversed: false,
    disputed: false,
    authorId: null,
    lines: [],
    promises: [],
    dateRequest: null,
    ...overrides,
  });

  it("is a manager or an owner, for a debt that stands", () => {
    expect(canChangePromise(entry(), true)).toBe(true);
    expect(canChangePromise(entry({ kind: "opening" }), true)).toBe(true);
    expect(canChangePromise(entry(), false)).toBe(false);
    expect(canChangePromise(entry({ kind: "payment" }), true)).toBe(false);
    expect(canChangePromise(entry({ kind: "reversal" }), true)).toBe(false);
    expect(canChangePromise(entry({ reversed: true }), true)).toBe(false);
    expect(canChangePromise(entry({ createdAt: "?" }), true)).toBe(false);
  });

  it.each(["manager", "owner"] as const)("offers a %s the change on the credit entry only", async (role) => {
    await open(shop(), role);
    expect(screen.getAllByRole("button", { name: CHANGE })).toHaveLength(1);
    expect(within(creditRow()).getByRole("button", { name: CHANGE })).toBeTruthy();
  });

  it("shows a seller the history and an open request, but none of the actions", async () => {
    const server = shop([
      entryBody({
        id: CREDIT_ID,
        amount: 140000,
        promised_date: "2026-11-10",
        promises: [promiseBody(), promiseBody({ promised_date: "2026-11-10", actor: "staff", reason: "Kelishildi", created_at: "2026-10-06T05:00:00+00:00" })],
        date_request: dateRequestBody(),
      }),
    ]);
    await open(server, "seller");
    const row = creditRow();
    expect(within(row).getByText("Muddat tarixi")).toBeTruthy();
    expect(within(row).getByText("2026-yil 10-noyabr (hozirgi muddat)")).toBeTruthy();
    expect(within(row).getByText("Mijoz muddatni 2026-yil 20-noyabr ga ko'chirishni so'ragan.")).toBeTruthy();
    expect(within(row).getByText("Mijoz sababi: Oylik kechikdi")).toBeTruthy();
    expect(screen.queryByRole("button", { name: CHANGE })).toBeNull();
    expect(screen.queryByRole("link", { name: "Muddat so'rovlari" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Qabul qilish" })).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });
});

describe("the promise history and the open request on the staff card", () => {
  it("lists every date with where it came from, in the shop's words", async () => {
    await open(
      shop([
        entryBody({
          id: CREDIT_ID,
          promised_date: "2026-11-20",
          promises: [
            promiseBody({ actor: "staff" }),
            promiseBody({ promised_date: "2026-11-20", actor: "customer_request", reason: "Oylik kechikdi", created_at: "2026-10-07T05:00:00+00:00" }),
          ],
        }),
      ]),
    );
    const items = within(within(creditRow()).getByRole("list")).getAllByRole("listitem");
    expect(items.map((item) => [...item.children].map((part) => part.textContent))).toEqual([
      ["2026-yil 5-noyabr", "do'kon belgilagan, 2026-yil 6-oktabr, 00:30"],
      ["2026-yil 20-noyabr (hozirgi muddat)", "mijoz so'rovi bo'yicha, 2026-yil 7-oktabr, 10:00", "Sabab: Oylik kechikdi"],
    ]);
  });

  it("draws no history for an entry whose date never changed", async () => {
    await open(shop([entryBody({ id: CREDIT_ID, promises: [promiseBody()] })]));
    expect(screen.queryByText("Muddat tarixi")).toBeNull();
  });

  it("shows a manager the open request with the way to the list where it is answered", async () => {
    await open(shop([entryBody({ id: CREDIT_ID, date_request: dateRequestBody({ reason: null }) })]));
    const row = creditRow();
    expect(within(row).getByText("Mijoz muddatni 2026-yil 20-noyabr ga ko'chirishni so'ragan.")).toBeTruthy();
    expect(within(row).getByRole("link", { name: "Muddat so'rovlari" }).getAttribute("href")).toBe("#/date-requests");
    expect(within(row).queryByText(/Mijoz sababi/)).toBeNull();
  });

  it.each(["accepted", "declined", "expired"])("does not show a request that is %s as open", async (status) => {
    await open(shop([entryBody({ id: CREDIT_ID, date_request: dateRequestBody({ status, closed_at: "2026-10-06T06:00:00+00:00" }) })]));
    expect(screen.queryByText(/ko'chirishni so'ragan/)).toBeNull();
  });
});

describe("changing the promised date", () => {
  it("opens a date field bound to the sale and an optional reason, and sends nothing yet", async () => {
    const server = shop();
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: CHANGE }));
    const date = screen.getByLabelText(LABEL) as HTMLInputElement;
    expect([date.type, date.min, date.max]).toEqual(["date", "2026-10-06", "2027-10-06"]);
    expect(screen.getByText("2026-yil 6-oktabr dan 2027-yil 6-oktabr gacha bo'lgan kunni tanlash mumkin.")).toBeTruthy();
    expect(screen.getByText("Sabab yozilsa, mijozga yuboriladi. 300 belgigacha.")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Bekor qilish" }));
    expect(screen.queryByLabelText(LABEL)).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });

  it.each([
    ["", "Sanani tanlang."],
    ["2026-11-05", "Yozuvning muddati allaqachon shu sana."],
    ["2026-10-05", "Sana savdo kunidan oldin bo'lishi mumkin emas."],
    ["2027-10-07", "Sana savdo kunidan ko'pi bilan 365 kun keyin bo'lishi mumkin."],
  ])("sends nothing for the date %j and says why", async (date, message) => {
    const server = shop();
    await open(server);
    fill(date);
    expect(screen.getByText(message).id).toBe(`promise-${CREDIT_ID}-date-error`);
    expect(server.writes()).toHaveLength(0);
  });

  it("sends nothing while the reason is too long", async () => {
    const server = shop();
    await open(server);
    fill("2026-11-25", "a".repeat(301));
    expect(screen.getByText("Sabab 300 belgidan oshmasligi kerak.")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
  });

  it("sends the date and the reason once with an idempotency key, and says what changed", async () => {
    const server = shop();
    await open(server);
    server.held.entries = [PAYMENT, entryBody({ id: CREDIT_ID, amount: 140000, promised_date: "2026-11-25" })];
    fill("2026-11-25", " Kelishildi ");
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Muddat o'zgartirildi: 2026-yil 5-noyabr o'rniga 2026-yil 25-noyabr."));
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({
      method: "POST",
      path: `${SHOP_BASE}/entries/${CREDIT_ID}/promise`,
      body: { promised_date: "2026-11-25", reason: "Kelishildi" },
    });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    // The card is read again and shows the new date; the message outlives that reload.
    await waitFor(() => expect(within(creditRow()).getByText("To'lash va'dasi: 2026-yil 25-noyabr")).toBeTruthy());
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.queryByLabelText(LABEL)).toBeNull();
  });

  it("may set an earlier date, and sends no reason when none was written", async () => {
    const server = shop([PAYMENT, CREDIT], () => ok(changed("2026-10-20")));
    await open(server);
    fill("2026-10-20");
    await screen.findByRole("status");
    expect(server.writes()[0]?.body).toEqual({ promised_date: "2026-10-20" });
  });

  it("says, from the response, that the change closed the customer's request as accepted", async () => {
    const open_ = entryBody({ id: CREDIT_ID, amount: 140000, date_request: dateRequestBody() });
    const server = shop([open_], () => ok(changed("2026-11-25", dateRequestBody({ status: "accepted", closed_at: "2026-10-06T07:00:00+00:00" }))));
    await open(server);
    fill("2026-11-25");
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("Mijozning muddatni 2026-yil 20-noyabr ga ko'chirish so'rovi qabul qilingan deb yopildi.");
    expect(status.textContent).not.toContain("ochiq qoldi");
  });

  it("says, from the response, that the customer's request stays open when the new date is earlier than it asks", async () => {
    const open_ = entryBody({ id: CREDIT_ID, amount: 140000, date_request: dateRequestBody() });
    const server = shop([open_], () => ok(changed("2026-11-10", null)));
    await open(server);
    fill("2026-11-10");
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("Mijozning so'rovi ochiq qoldi: u 2026-yil 20-noyabr ni so'ragan.");
    expect(status.textContent).not.toContain("yopildi");
  });

  it("says nothing about a request when there was none", async () => {
    const server = shop();
    await open(server);
    fill("2026-11-25");
    const status = await screen.findByRole("status");
    expect(status.querySelectorAll("p")).toHaveLength(1);
  });

  it("disables the form while the change is in flight, so a double tap sends one", async () => {
    const gate = deferred<ReturnType<typeof ok>>();
    const server = shop([PAYMENT, CREDIT], () => gate.promise);
    await open(server);
    fill("2026-11-25");
    const button = screen.getByRole("button", { name: "Saqlanmoqda…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    fireEvent.submit(button.closest("form") as HTMLFormElement);
    gate.resolve(ok(changed("2026-11-25")));
    await screen.findByRole("status");
    expect(server.writes()).toHaveLength(1);
  });

  it.each([
    ["PROMISE_BEFORE_SALE", "Sana savdo kunidan oldin bo'lishi mumkin emas."],
    ["PROMISE_TOO_FAR", "Sana savdo kunidan ko'pi bilan 365 kun keyin bo'lishi mumkin."],
    ["PROMISE_UNCHANGED", "Yozuvning muddati allaqachon shu sana."],
  ])("puts the server's %s next to the date, and a retry reuses the key", async (code, message) => {
    const server = shop([PAYMENT, CREDIT], () =>
      refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { promised_date: code }),
    );
    await open(server);
    fill("2026-11-25");
    expect((await screen.findByText(message)).id).toBe(`promise-${CREDIT_ID}-date-error`);
    fireEvent.click(screen.getByRole("button", { name: "Muddatni saqlash" }));
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[1]?.headers["Idempotency-Key"]).toBe(server.writes()[0]?.headers["Idempotency-Key"]);
  });

  it.each([
    ["reversed", "Yozuv bekor qilingan: uning muddati o'zgartirilmaydi."],
    ["not_a_debt", "Faqat nasiya yoki boshlang'ich qarz yozuvining muddati bor."],
  ])("explains PROMISE_NOT_CHANGEABLE: %s", async (reason, detail) => {
    const general = "Bu yozuvning to'lash muddati yo'q yoki yozuv bekor qilingan.";
    const server = shop([PAYMENT, CREDIT], () => refusal(409, "PROMISE_NOT_CHANGEABLE", general, { reason }));
    await open(server);
    fill("2026-11-25");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(general);
    expect(alert.textContent).toContain(detail);
  });

  it("shows any other refusal with the server's message", async () => {
    const server = shop([PAYMENT, CREDIT], () => refusal(403, "FORBIDDEN_ROLE", "Bu amal uchun sizning rolingiz yetarli emas."));
    await open(server);
    fill("2026-11-25");
    expect((await screen.findByRole("alert")).textContent).toContain("rolingiz yetarli emas");
    expect(screen.queryByRole("status")).toBeNull();
  });
});
