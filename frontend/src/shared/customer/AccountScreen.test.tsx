// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { I18nProvider } from "../../i18n/I18nProvider";
import type { Language } from "../../i18n/types";
import {
  accountBody,
  accountEntryBody,
  deferred,
  DISPUTE_ID,
  disputeBody,
  fakeServer,
  lineBody,
  LINK_ID,
  ME_BASE,
  ok,
  refusal,
  type Reply,
  type Sent,
} from "../../testing/fakeServer";
import { exact } from "../../testing/renderScreen";
import { type AccountEntry, createApi } from "../api";
import { AccountScreen, canDispute } from "./AccountScreen";

afterEach(cleanup);

const CREDIT_ID = "22222222-2222-4222-8222-222222222222";
const PAYMENT_ID = "66666666-6666-4666-8666-666666666666";
const PAYMENT = accountEntryBody({
  id: PAYMENT_ID,
  kind: "payment",
  amount: 20000,
  created_at: "2026-10-06T06:05:00+00:00",
  promised_date: null,
});
const CREDIT = accountEntryBody({ id: CREDIT_ID, amount: 140000 });
const REFUSED = "Bu yozuv bo'yicha e'tiroz bildirib bo'lmaydi yoki u allaqachon ko'rib chiqilgan.";

/** The customer's account; `onWrite` answers anything that is not a read. */
function backend(account: () => Reply = () => ok(accountBody({ entries: [PAYMENT, CREDIT], entries_total: 2 })), onWrite: (sent: Sent, attempt: number) => Reply = () => ok({})) {
  let attempt = 0;
  return fakeServer((sent) => (sent.method === "GET" ? account() : onWrite(sent, attempt++)));
}

function show(server: ReturnType<typeof fakeServer>, language: Language = "uz") {
  const api = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "customer-session" } }).account(LINK_ID);
  return render(
    <I18nProvider initialLanguage={language}>
      <AccountScreen api={api} back={<a href="#/my">back</a>} />
    </I18nProvider>,
  );
}

async function open(server: ReturnType<typeof fakeServer>, language: Language = "uz") {
  const view = show(server, language);
  await screen.findByRole("heading", { level: 2, name: "Baraka savdo" });
  return view;
}

const entryRows = () =>
  within(screen.getByRole("region", { name: /Yozuvlar|Записи/ }))
    .getAllByRole("listitem")
    .filter((item) => item.classList.contains("row"));
const manage = () => screen.getByRole("region", { name: "Aloqa va ma'lumotlar" });

describe("the customer's own account", () => {
  it("reads only their own account and shows the shop, the balance and what is late", async () => {
    const server = backend(() => ok(accountBody({ overdue: { amount: 45000, due_today: 10000 } })));
    show(server);
    expect(screen.getByRole("status").textContent).toBe("Yuklanmoqda…");
    await screen.findByRole("heading", { level: 2, name: "Baraka savdo" });

    expect(server.sent).toHaveLength(1);
    expect(server.sent[0]).toMatchObject({ method: "GET", path: ME_BASE });
    expect(server.sent[0]?.headers["Authorization"]).toBe("Bearer customer-session");
    expect(screen.getByText("Do'kon daftarida ismingiz: Ali Valiyev")).toBeTruthy();
    expect(screen.getByText(exact("120 000 so'm"), { selector: ".balance strong" })).toBeTruthy();
    expect(screen.getByText(exact("45 000 so'm muddati o'tgan"))).toBeTruthy();
    expect(screen.getByText(exact("Bugun to'lanishi kerak: 10 000 so'm"))).toBeTruthy();
  });

  it("shows nothing about lateness when nothing is late", async () => {
    await open(backend());
    expect(document.body.textContent).not.toContain("muddati o'tgan");
    expect(document.body.textContent).not.toContain("Bugun to'lanishi kerak");
  });

  it("lists the entries in the server's order, newest first, with dates in Tashkent time", async () => {
    await open(backend());
    const [newest, oldest] = entryRows();
    expect(entryRows()).toHaveLength(2);
    expect(newest?.textContent).toContain("To'lov");
    expect(newest?.textContent).toContain("20 000 so'm");
    expect(newest?.textContent).toContain("2026-yil 6-oktabr, 11:05"); // 06:05 UTC
    expect(oldest?.textContent).toContain("Nasiya");
    expect(oldest?.textContent).toContain("140 000 so'm");
    // 19:30 UTC on 5 October is 00:30 on 6 October in Tashkent.
    expect(oldest?.textContent).toContain("2026-yil 6-oktabr, 00:30");
    expect(oldest?.textContent).toContain("To'lash va'dasi: 2026-yil 5-noyabr");
  });

  it("shows the goods of an entry, and none for an entry that came without the field", async () => {
    const bare: Record<string, unknown> = { ...PAYMENT };
    delete bare["lines"];
    const withGoods = { ...CREDIT, lines: [lineBody({ name: "Shakar", qty: "1.500", unit: "kg", unit_price: 14000, line_total: 21000 })] };
    await open(backend(() => ok(accountBody({ entries: [bare, withGoods], entries_total: 2 }))));
    const [payment, credit] = entryRows();
    expect(within(payment as HTMLElement).queryByRole("list")).toBeNull();
    const goods = within(credit as HTMLElement).getByRole("list", { name: "Tovarlar" });
    expect(goods.textContent).toContain("Shakar");
    expect(goods.textContent).toContain("21 000 so'm");
    expect(goods.textContent).toContain("1,5 kg × 14 000 so'm");
  });

  it("marks a reversed entry and hides its promised date", async () => {
    await open(
      backend(() =>
        ok(
          accountBody({
            entries: [
              accountEntryBody({ id: "r1", kind: "reversal", amount: 140000, reverses_id: CREDIT_ID, promised_date: null }),
              { ...CREDIT, reversed: true },
            ],
            entries_total: 2,
          }),
        ),
      ),
    );
    const [reversal, credit] = entryRows();
    expect(reversal?.textContent).toContain("Bekor qilish yozuvi");
    expect(credit?.className).toContain("row--struck");
    expect(credit?.textContent).toContain("Bekor qilingan");
    expect(credit?.textContent).not.toContain("To'lash va'dasi");
  });

  it("says how many entries are not shown", async () => {
    await open(backend(() => ok(accountBody({ entries_total: 73 }))));
    expect(screen.getByText("Oxirgi yozuvlar ko'rsatilgan: 1 ta, jami 73 ta.")).toBeTruthy();
  });

  it("says when there is no entry yet", async () => {
    await open(backend(() => ok(accountBody({ balance: 0, entries: [], entries_total: 0 }))));
    expect(screen.getByText("Hali yozuv yo'q.")).toBeTruthy();
  });

  it("shows the server's message when the account cannot be read, and retries", async () => {
    let fail = true;
    const server = backend(() => (fail ? "offline" : ok(accountBody())));
    show(server);
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi.");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    await screen.findByRole("heading", { level: 2, name: "Baraka savdo" });
  });

  it("shows an account that is not theirs or is gone as not found, with the way back", async () => {
    show(backend(() => refusal(404, "NOT_FOUND", "Topilmadi.")));
    expect(await screen.findByText("Bu hisob topilmadi yoki do'kon bilan aloqa uzilgan.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "back" })).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("refuses an answer that is not whole UZS instead of showing it", async () => {
    show(backend(() => ok(accountBody({ balance: 120000.5 }))));
    expect((await screen.findByRole("alert")).textContent).toContain("Xatolik yuz berdi");
    expect(document.body.textContent).not.toContain("120");
  });
});

describe("there is no confirming of an entry (BR-10)", () => {
  const EVERYTHING = () =>
    ok(
      accountBody({
        entries: [
          PAYMENT,
          CREDIT,
          accountEntryBody({ id: "o1", kind: "opening", amount: 5000, promised_date: null }),
          accountEntryBody({ id: "d1", disputed: true, dispute: disputeBody() }),
          accountEntryBody({ id: "d2", dispute: disputeBody({ id: "x2", status: "declined", decline_reason: "Imzo bor" }) }),
          accountEntryBody({ id: "r1", kind: "reversal", reverses_id: "d3", promised_date: null }),
          accountEntryBody({ id: "d3", reversed: true, dispute: disputeBody({ id: "x3", status: "reversed" }) }),
        ],
        entries_total: 7,
      }),
    );

  it.each(["uz", "ru"] as Language[])("offers nothing on an entry but disputing it, taking the dispute back, and asking for a later date (%s)", async (language) => {
    await open(backend(EVERYTHING), language);
    const allowed =
      language === "uz"
        ? ["E'tiroz bildirish", "E'tirozni qaytarib olish", "Muddatni kechroq so'rash"]
        : ["Возразить", "Отозвать возражение", "Попросить срок попозже"];
    const offered = entryRows().flatMap((row) =>
      [...within(row).queryAllByRole("button"), ...within(row).queryAllByRole("link"), ...within(row).queryAllByRole("checkbox")].map(
        (control) => control.textContent ?? "",
      ),
    );
    expect(offered.length).toBeGreaterThan(0);
    expect(offered.filter((name) => !allowed.includes(name))).toEqual([]);

    // Nor anywhere else on the page: no control is named after confirming, agreeing or accepting.
    const names = [...screen.queryAllByRole("button"), ...screen.queryAllByRole("link")].map((control) => control.textContent ?? "");
    expect(names.filter((name) => /tasdiq|rozi|qabul|подтвер|соглас|принять|confirm|accept/i.test(name))).toEqual([]);
    expect(document.querySelectorAll("input, select").length).toBe(0);
  });

  it("sends nothing by itself: reading the account is the only request", async () => {
    const server = backend(EVERYTHING);
    await open(server);
    expect(server.sent).toHaveLength(1);
    expect(server.writes()).toHaveLength(0);
  });
});

describe("which entries are offered a dispute", () => {
  const entry = (overrides: Partial<AccountEntry> = {}): AccountEntry => ({
    id: CREDIT_ID,
    kind: "credit",
    amount: 45000,
    createdAt: "2026-10-05T19:30:00+00:00",
    promisedDate: null,
    reversesId: null,
    reversed: false,
    disputed: false,
    dispute: null,
    lines: [],
    promises: [],
    dateRequest: null,
    ...overrides,
  });
  const closed = { id: DISPUTE_ID, reason: "x", declineReason: null };

  it("a credit sale or an opening debt that is not reversed and was never disputed", () => {
    expect(canDispute(entry())).toBe(true);
    expect(canDispute(entry({ kind: "opening" }))).toBe(true);
  });

  it.each([
    ["a payment", { kind: "payment" }],
    ["a reversal", { kind: "reversal" }],
    ["an unknown kind", { kind: "adjustment" }],
    ["a reversed sale", { reversed: true }],
    ["a sale with an open dispute", { disputed: true, dispute: { ...closed, status: "open" } }],
    ["a sale whose dispute was declined", { dispute: { ...closed, status: "declined" } }],
    ["a sale whose dispute was withdrawn", { dispute: { ...closed, status: "withdrawn" } }],
  ] as [string, Partial<AccountEntry>][])("not %s", (_what, overrides) => {
    expect(canDispute(entry(overrides))).toBe(false);
  });

  it("shows the button on exactly those entries", async () => {
    await open(
      backend(() =>
        ok(
          accountBody({
            entries: [
              PAYMENT,
              CREDIT,
              accountEntryBody({ id: "o1", kind: "opening", promised_date: null }),
              accountEntryBody({ id: "x1", reversed: true }),
              accountEntryBody({ id: "x2", dispute: disputeBody({ status: "withdrawn" }) }),
            ],
            entries_total: 5,
          }),
        ),
      ),
    );
    const offered = entryRows().map((row) => within(row).queryByRole("button", { name: "E'tiroz bildirish" }) !== null);
    expect(offered).toEqual([false, true, true, false, false]);
  });
});

describe("disputing an entry", () => {
  const reasonField = () => screen.getByRole("textbox", { name: "Nima noto'g'ri?" });
  const creditRow = () => entryRows()[1] as HTMLElement;

  async function disputeWith(server: ReturnType<typeof fakeServer>, reason: string) {
    await open(server);
    fireEvent.click(within(creditRow()).getByRole("button", { name: "E'tiroz bildirish" }));
    fireEvent.change(reasonField(), { target: { value: reason } });
    fireEvent.click(screen.getByRole("button", { name: "E'tirozni yuborish" }));
  }

  it("posts the entry and the tidied reason, without an idempotency key, and shows the dispute", async () => {
    let disputed = false;
    const server = backend(
      () =>
        ok(
          accountBody({
            entries: [PAYMENT, disputed ? { ...CREDIT, disputed: true, dispute: disputeBody({ reason: "Men bu tovarni olmaganman" }) } : CREDIT],
            entries_total: 2,
          }),
        ),
      () => {
        disputed = true;
        return ok({ ...disputeBody(), entry_id: CREDIT_ID, created_at: "2026-10-06T07:00:00+00:00" }, 201);
      },
    );
    await disputeWith(server, "  Men bu tovarni \n olmaganman ");

    expect(await screen.findByText("E'tiroz yuborilgan, do'kon javobi kutilmoqda.")).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({
      method: "POST",
      path: `${ME_BASE}/disputes`,
      body: { entry_id: CREDIT_ID, reason: "Men bu tovarni olmaganman" },
    });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toBeUndefined();
    expect(creditRow().textContent).toContain("Sizning sababingiz: Men bu tovarni olmaganman");
    expect(within(creditRow()).queryByRole("button", { name: "E'tiroz bildirish" })).toBeNull();
  });

  it.each([
    ["no reason", ""],
    ["two characters", "yo"],
    ["only spaces", "      "],
    ["301 characters", "x".repeat(301)],
  ])("refuses %s before anything is sent", async (_what, reason) => {
    const server = backend();
    await disputeWith(server, reason);
    expect(screen.getByRole("alert").textContent).toBe("Sabab 3 dan 300 belgigacha bo'lishi kerak.");
    expect(server.writes()).toHaveLength(0);
  });

  it.each([
    ["three characters", "yo'"],
    ["300 characters", "x".repeat(300)],
  ])("accepts %s", async (_what, reason) => {
    const server = backend();
    await disputeWith(server, reason);
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ entry_id: CREDIT_ID, reason });
  });

  it.each([
    ["not_a_debt", "Faqat qarzni oshiradigan yozuvga e'tiroz bildiriladi."],
    ["reversed", "Do'kon bu yozuvni allaqachon bekor qilgan."],
    ["already_disputed", "Bu yozuvga bir marta e'tiroz bildirilgan; ikkinchi marta bo'lmaydi."],
    ["too_late", "Bu yozuvga e'tiroz bildirish muddati o'tgan."],
  ])("shows the server's refusal and why (%s), and keeps the text", async (reason, why) => {
    const server = backend(undefined, () => refusal(409, "DISPUTE_NOT_ALLOWED", REFUSED, { reason }));
    await disputeWith(server, "Men olmaganman");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(REFUSED);
    expect(alert.textContent).toContain(why);
    expect((reasonField() as HTMLTextAreaElement).value).toBe("Men olmaganman");
    expect(document.body.textContent).not.toContain("do'kon javobi kutilmoqda");
  });

  it("shows the server's message alone when it gives a reason this page does not know", async () => {
    const server = backend(undefined, () => refusal(409, "DISPUTE_NOT_ALLOWED", REFUSED, { reason: "something_new" }));
    await disputeWith(server, "Men olmaganman");
    expect((await screen.findByRole("alert")).textContent).toBe(REFUSED);
  });

  it("sends one request for a double tap and disables the form while it is pending", async () => {
    const held = deferred<Reply>();
    const server = backend(undefined, () => held.promise as Reply);
    await disputeWith(server, "Men olmaganman");
    const pending = screen.getByRole("button", { name: "Saqlanmoqda…" }) as HTMLButtonElement;
    expect(pending.disabled).toBe(true);
    fireEvent.submit(pending.closest("form") as HTMLFormElement);
    held.resolve(ok(disputeBody(), 201));
    await waitFor(() => expect(server.sent.filter((sent) => sent.method === "GET")).toHaveLength(2));
    expect(server.writes()).toHaveLength(1);
  });

  it("closes the form on cancel and sends nothing", async () => {
    const server = backend();
    await open(server);
    fireEvent.click(within(creditRow()).getByRole("button", { name: "E'tiroz bildirish" }));
    fireEvent.click(screen.getByRole("button", { name: "Bekor qilish" }));
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });
});

describe("a dispute on an entry", () => {
  const withDispute = (dispute: unknown) => () =>
    ok(accountBody({ entries: [{ ...CREDIT, disputed: true, dispute }], entries_total: 1 }));

  it.each([
    ["open", "E'tiroz yuborilgan, do'kon javobi kutilmoqda."],
    ["declined", "Do'kon e'tirozni rad etdi."],
    ["withdrawn", "E'tirozni qaytarib olgansiz."],
    ["reversed", "Do'kon e'tirozni qabul qildi: yozuv bekor qilingan."],
    ["archived", "E'tiroz yopilgan."],
  ])("shows the status %s and the customer's reason", async (status, text) => {
    await open(backend(withDispute(disputeBody({ status }))));
    const row = entryRows()[0] as HTMLElement;
    expect(within(row).getByText(text)).toBeTruthy();
    expect(row.textContent).toContain("Sizning sababingiz: Men bu tovarni olmaganman");
    expect(row.textContent).not.toContain("Do'kon sababi");
  });

  it("shows the shop's reason when the dispute was declined", async () => {
    await open(backend(withDispute(disputeBody({ status: "declined", decline_reason: "Tovar berilgan, imzo bor" }))));
    expect(screen.getByText("Do'kon sababi: Tovar berilgan, imzo bor")).toBeTruthy();
  });

  it("offers to withdraw an open dispute only", async () => {
    await open(backend(withDispute(disputeBody())));
    expect(screen.getByRole("button", { name: "E'tirozni qaytarib olish" })).toBeTruthy();
    cleanup();
    for (const status of ["declined", "withdrawn", "reversed"]) {
      await open(backend(withDispute(disputeBody({ status }))));
      expect(screen.queryByRole("button", { name: "E'tirozni qaytarib olish" })).toBeNull();
      cleanup();
    }
  });

  it("withdraws with one request, without an idempotency key, and shows the account again", async () => {
    let withdrawn = false;
    const held = deferred<Reply>();
    const server = backend(
      () => withDispute(disputeBody({ status: withdrawn ? "withdrawn" : "open" }))(),
      () => {
        withdrawn = true;
        return held.promise as Reply;
      },
    );
    await open(server);
    const button = screen.getByRole("button", { name: "E'tirozni qaytarib olish" }) as HTMLButtonElement;
    fireEvent.click(button);
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    held.resolve(ok(disputeBody({ status: "withdrawn" })));

    expect(await screen.findByText("E'tirozni qaytarib olgansiz.")).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${ME_BASE}/disputes/${DISPUTE_ID}/withdraw` });
    expect(server.writes()[0]?.body).toBeUndefined();
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toBeUndefined();
  });

  it("shows the server's refusal to withdraw and why", async () => {
    const server = backend(withDispute(disputeBody()), () => refusal(409, "DISPUTE_NOT_ALLOWED", REFUSED, { reason: "not_open" }));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "E'tirozni qaytarib olish" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(REFUSED);
    expect(alert.textContent).toContain("Bu e'tiroz allaqachon yopilgan.");
  });
});

describe("disconnecting from the shop", () => {
  it("asks first with what it means, and sends nothing until the answer is yes", async () => {
    const server = backend();
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Do'kondan uzilish" }));
    expect(server.writes()).toHaveLength(0);
    expect(manage().textContent).toContain("«Baraka savdo» do'konidan uzilasizmi?");
    expect(manage().textContent).toContain("Qarzning o'zi o'zgarmaydi.");

    fireEvent.click(screen.getByRole("button", { name: "Bekor qilish" }));
    expect(server.writes()).toHaveLength(0);
    expect(manage().textContent).not.toContain("uzilasizmi");
    expect(screen.getByRole("button", { name: "Do'kondan uzilish" })).toBeTruthy();
  });

  it("disconnects with one request and then shows no account data", async () => {
    const held = deferred<Reply>();
    const server = backend(undefined, () => held.promise as Reply);
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Do'kondan uzilish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, uzilaman" }));
    const pending = screen.getByRole("button", { name: "Saqlanmoqda…" }) as HTMLButtonElement;
    expect(pending.disabled).toBe(true);
    fireEvent.click(pending);
    held.resolve(ok({ disconnected: true }));

    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toBe(
        "Do'kondan uzildingiz. Qayta ulanish uchun do'kondan yangi havola so'rang.",
      ),
    );
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${ME_BASE}/disconnect` });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toBeUndefined();
    expect(document.body.textContent).not.toContain("120 000");
    expect(screen.queryByRole("region")).toBeNull();
    expect(screen.getByRole("link", { name: "back" })).toBeTruthy();
  });

  it("shows the server's refusal and stays connected", async () => {
    const server = backend(undefined, () => refusal(404, "NOT_FOUND", "Topilmadi."));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Do'kondan uzilish" }));
    fireEvent.click(screen.getByRole("button", { name: "Ha, uzilaman" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Topilmadi.");
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByRole("heading", { level: 2, name: "Baraka savdo" })).toBeTruthy();
  });
});

describe("asking for removal of their data", () => {
  const ask = () => fireEvent.click(screen.getByRole("button", { name: "Ma'lumotlarimni o'chirishni so'rash" }));

  it("asks first with the consequences, and sends nothing until the answer is yes", async () => {
    const server = backend();
    await open(server);
    ask();
    expect(server.writes()).toHaveLength(0);
    expect(manage().textContent).toContain("Bu do'kondagi shaxsiy ma'lumotlaringiz o'chirilsinmi?");
    expect(manage().textContent).toContain("Buni ortga qaytarib bo'lmaydi.");
    expect(manage().textContent).toContain("Qarzingiz bo'lsa, ma'lumotlar qarz to'liq to'langanidan keyingina o'chiriladi.");

    fireEvent.click(screen.getByRole("button", { name: "Bekor qilish" }));
    expect(server.writes()).toHaveLength(0);
    expect(manage().textContent).not.toContain("ortga qaytarib");
  });

  it("shows the server's answer when the data waits for the balance", async () => {
    let requested = false;
    const server = backend(
      () => ok(accountBody({ removal_requested: requested })),
      () => {
        requested = true;
        return ok({ removed: false, waiting_for_balance: 120000 });
      },
    );
    await open(server);
    ask();
    fireEvent.click(screen.getByRole("button", { name: "Ha, o'chirilsin" }));
    expect((await screen.findByText(/So'rov qabul qilindi/)).textContent).toBe(
      "So'rov qabul qilindi. Ma'lumotlaringiz 120 000 so'm qarz to'langanidan keyin o'chiriladi.",
    );
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${ME_BASE}/removal` });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toBeUndefined();
    // The account is still there, and the request is not offered a second time.
    expect(await screen.findByRole("button", { name: "Do'kondan uzilish" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Baraka savdo" })).toBeTruthy();
    expect(server.sent.filter((sent) => sent.method === "GET")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Ma'lumotlarimni o'chirishni so'rash" })).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("So'rov qabul qilindi.");
  });

  it("shows the server's answer when the data is removed at once, and no account data after it", async () => {
    const server = backend(undefined, () => ok({ removed: true, waiting_for_balance: null }));
    await open(server);
    ask();
    fireEvent.click(screen.getByRole("button", { name: "Ha, o'chirilsin" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Ma'lumotlaringiz o'chirildi."));
    expect(server.sent.filter((sent) => sent.method === "GET")).toHaveLength(1);
    expect(document.body.textContent).not.toContain("Ali Valiyev");
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("says a removal was already requested and does not offer it again", async () => {
    await open(backend(() => ok(accountBody({ removal_requested: true }))));
    expect(screen.getByText("Ma'lumotlarni o'chirish so'ralgan: qarz to'liq to'langanidan keyin o'chiriladi.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Ma'lumotlarimni o'chirishni so'rash" })).toBeNull();
  });

  it("shows the server's refusal and sends one request for a double tap", async () => {
    const held = deferred<Reply>();
    const server = backend(undefined, () => held.promise as Reply);
    await open(server);
    ask();
    fireEvent.click(screen.getByRole("button", { name: "Ha, o'chirilsin" }));
    fireEvent.click(screen.getByRole("button", { name: "Saqlanmoqda…" }));
    held.resolve(refusal(404, "NOT_FOUND", "Topilmadi."));
    expect((await screen.findByRole("alert")).textContent).toBe("Topilmadi.");
    expect(server.writes()).toHaveLength(1);
    expect(screen.queryByRole("status")).toBeNull();
  });
});
