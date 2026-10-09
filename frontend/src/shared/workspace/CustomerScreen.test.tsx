// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  creditSettingsBody,
  CUSTOMER_ID,
  customerBody,
  deferred,
  detailBody,
  entryBody,
  fakeServer,
  lineBody,
  linkBody,
  ok,
  refusal,
  type Reply,
  type Sent,
  SHOP_BASE,
} from "../../testing/fakeServer";
import { exact, renderScreen } from "../../testing/renderScreen";
import type { Entry } from "../api";
import type { Role } from "../navigation";
import { canReverse, CustomerScreen } from "./CustomerScreen";

afterEach(cleanup);

const CREDIT_ID = "22222222-2222-4222-8222-222222222222";
const PAYMENT_ID = "66666666-6666-4666-8666-666666666666";

const PAYMENT = entryBody({
  id: PAYMENT_ID,
  seq: 2,
  kind: "payment",
  amount: 20000,
  note: "naqd",
  created_at: "2026-10-06T06:05:00+00:00",
  promised_date: null,
});
const CREDIT = entryBody({ id: CREDIT_ID, seq: 1, amount: 140000, note: "un va yog'" });

/** A customer who bought for 140 000 and paid 20 000; `onWrite` answers anything that is not a read. */
function shop(onWrite: (sent: Sent, attempt: number) => Reply = () => ok({}), detail: () => Reply = () => ok(detailBody({ entries: [PAYMENT, CREDIT], entries_total: 2 }))) {
  let attempt = 0;
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      return onWrite(sent, attempt++);
    }
    if (sent.path.endsWith("/credit-settings")) {
      return ok(creditSettingsBody());
    }
    return sent.path.endsWith("/link") ? ok(linkBody()) : detail();
  });
}

async function open(server: ReturnType<typeof fakeServer>, role: Role = "manager") {
  renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: server.fetch, role });
  await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
}

const entryRows = () => within(screen.getByRole("region", { name: "Yozuvlar" })).getAllByRole("listitem");
const reverseButtons = () => screen.queryAllByRole("button", { name: "Yozuvni bekor qilish" });

describe("customer page", () => {
  it("shows the balance, the phone and the entries, newest first, in Tashkent time", async () => {
    const server = shop();
    renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: server.fetch });
    expect(screen.getByRole("status").textContent).toBe("Yuklanmoqda…");
    await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });

    expect(server.sent[0]).toMatchObject({ method: "GET", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}` });
    expect(screen.getByText(exact("120\u00a0000 so'm"))).toBeTruthy();
    expect(screen.getByRole("link", { name: "+998901234567" }).getAttribute("href")).toBe("tel:+998901234567");

    const [newest, oldest] = entryRows();
    expect(newest?.textContent).toContain("To'lov");
    expect(newest?.textContent).toContain("20\u00a0000 so'm");
    expect(newest?.textContent).toContain("2026-yil 6-oktabr, 11:05"); // 06:05 UTC
    expect(newest?.textContent).toContain("naqd");
    expect(oldest?.textContent).toContain("Nasiya");
    expect(oldest?.textContent).toContain("140\u00a0000 so'm");
    // 19:30 UTC on 5 October is 00:30 on 6 October in Tashkent.
    expect(oldest?.textContent).toContain("2026-yil 6-oktabr, 00:30");
    expect(oldest?.textContent).not.toContain("5-oktabr");
    expect(oldest?.textContent).toContain("To'lash va'dasi: 2026-yil 5-noyabr");
  });

  it("links to a credit sale and to a payment", async () => {
    await open(shop());
    expect(screen.getByRole("link", { name: "Nasiya yozish" }).getAttribute("href")).toBe(`#/customers/${CUSTOMER_ID}/credit`);
    expect(screen.getByRole("link", { name: "To'lov qabul qilish" }).getAttribute("href")).toBe(
      `#/customers/${CUSTOMER_ID}/payment`,
    );
  });

  it("offers no payment when nothing is owed, and no new entry for an archived customer", async () => {
    await open(shop(undefined, () => ok(detailBody({ balance: 0, entries: [], entries_total: 0 }))));
    expect(screen.getByRole("link", { name: "Nasiya yozish" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "To'lov qabul qilish" })).toBeNull();
    expect(screen.getByText("Hali yozuv yo'q.")).toBeTruthy();
    cleanup();

    await open(shop(undefined, () => ok(detailBody({ status: "archived", balance: 0 }))));
    expect(screen.getByText("Bu mijoz arxivda. Yangi yozuv uchun avval arxivdan chiqaring.")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Nasiya yozish" })).toBeNull();
    expect(screen.queryByRole("link", { name: "To'lov qabul qilish" })).toBeNull();
  });

  it("shows what is overdue, the payment history, and marks on entries", async () => {
    const server = shop(undefined, () =>
      ok(
        detailBody({
          overdue: { amount: 45000, since: "2026-09-24", days: 12, due_today: 0 },
          payment_history: { on_time_percent: 67, on_time_amount: 200000, due_amount: 300000, longest_delay_days: 9 },
          reminders_off: true,
          entries: [
            entryBody({ id: "r1", seq: 3, kind: "reversal", amount: 20000, reverses_id: PAYMENT_ID, promised_date: null }),
            { ...PAYMENT, reversed: true },
            { ...CREDIT, disputed: true },
          ],
          entries_total: 240,
        }),
      ),
    );
    await open(server);
    expect(screen.getByText("45\u00a0000 so'm muddati o'tgan", { normalizer: (text) => text })).toBeTruthy();
    expect(screen.getByText("12 kun kechikkan")).toBeTruthy();
    expect(screen.getByText("O'z vaqtida to'langan: 67%.")).toBeTruthy();
    expect(screen.getByText("Eng uzoq kechikish: 9 kun.")).toBeTruthy();
    expect(screen.getByText("Eslatmalar o'chirilgan")).toBeTruthy();

    const [reversal, reversedPayment, disputed] = entryRows();
    expect(reversal?.textContent).toContain("Bekor qilish yozuvi");
    expect(reversedPayment?.textContent).toContain("Bekor qilingan");
    expect(disputed?.textContent).toContain("Mijoz e'tiroz bildirgan");
    expect(screen.getByText("Oxirgi yozuvlar ko'rsatilgan: 3 ta, jami 240 ta.")).toBeTruthy();
  });

  it("shows the not-found text for an unknown customer, and a retry for a failed load", async () => {
    const missing = fakeServer(() => refusal(404, "NOT_FOUND", "Topilmadi."));
    renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: missing.fetch });
    expect(await screen.findByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    cleanup();

    let attempt = 0;
    const flaky = fakeServer(() => (attempt++ === 0 ? "offline" : ok(detailBody())));
    renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: flaky.fetch });
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi.");
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" })).toBeTruthy();
  });
});

describe("what a role is offered (REQ-033)", () => {
  it("offers a seller no reversal, no rename and no archive", async () => {
    await open(shop(), "seller");
    expect(reverseButtons()).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Tahrirlash" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Arxivlash" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Mijozni boshqarish" })).toBeNull();
    // A seller still records sales and payments.
    expect(screen.getByRole("link", { name: "Nasiya yozish" })).toBeTruthy();
    expect(entryRows()).toHaveLength(2);
  });

  it.each(["manager", "owner"] as const)("offers a %s reversal, rename and archive", async (role) => {
    await open(shop(), role);
    expect(reverseButtons()).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Tahrirlash" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Arxivlash" })).toBeTruthy();
  });

  it("offers a reversal only for an entry the server would accept", () => {
    const entry = (overrides: Partial<Entry>): Entry => ({
      id: "e",
      seq: 1,
      kind: "credit",
      amount: 45000,
      note: null,
      createdAt: "2026-10-06T07:00:00+00:00",
      promisedDate: null,
      reversesId: null,
      reversed: false,
      disputed: false,
      authorId: null,
      lines: [],
      promises: [],
      dateRequest: null,
      ...overrides,
    });
    expect(canReverse(entry({}), true)).toBe(true);
    expect(canReverse(entry({ kind: "payment" }), true)).toBe(true);
    expect(canReverse(entry({ kind: "opening" }), true)).toBe(true);
    expect(canReverse(entry({ kind: "reversal" }), true)).toBe(false);
    expect(canReverse(entry({ reversed: true }), true)).toBe(false);
    expect(canReverse(entry({}), false)).toBe(false);
  });

  it("shows no reversal button on a reversal or on a reversed entry, even to an owner", async () => {
    const server = shop(undefined, () =>
      ok(
        detailBody({
          entries: [
            entryBody({ id: "r1", seq: 3, kind: "reversal", amount: 20000, reverses_id: PAYMENT_ID, promised_date: null }),
            { ...PAYMENT, reversed: true },
            CREDIT,
          ],
          entries_total: 3,
        }),
      ),
    );
    await open(server, "owner");
    const [reversal, reversedPayment, credit] = entryRows();
    expect(within(reversal as HTMLElement).queryByRole("button")).toBeNull();
    expect(within(reversedPayment as HTMLElement).queryByRole("button")).toBeNull();
    expect(within(credit as HTMLElement).getByRole("button", { name: "Yozuvni bekor qilish" })).toBeTruthy();
  });
});

describe("reversal", () => {
  it("asks first, then posts one keyed reversal and shows the customer again", async () => {
    let reversed = false;
    const server = shop(
      () => {
        reversed = true;
        return ok({ entry: { id: "r1", kind: "reversal", amount: 20000 }, customer: customerBody({ balance: 140000 }) }, 201);
      },
      () =>
        ok(
          reversed
            ? detailBody({ balance: 140000, entries: [{ ...PAYMENT, reversed: true }, CREDIT], entries_total: 3 })
            : detailBody({ entries: [PAYMENT, CREDIT], entries_total: 2 }),
        ),
    );
    await open(server);
    const row = entryRows()[0] as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Yozuvni bekor qilish" }));
    // Nothing is sent until the manager confirms.
    expect(server.writes()).toHaveLength(0);
    expect(row.textContent).toContain("Bu yozuv (20\u00a0000 so'm) bekor qilinsinmi?");

    fireEvent.click(within(row).getByRole("button", { name: "Ha, bekor qilinsin" }));
    await screen.findByText("Bekor qilingan");

    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/entries/${PAYMENT_ID}/reversal` });
    expect(server.writes()[0]?.body).toBeUndefined();
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(screen.getByText(exact("140\u00a0000 so'm"), { selector: ".balance strong" })).toBeTruthy();
  });

  it("sends nothing when the manager answers no", async () => {
    const server = shop();
    await open(server);
    const row = entryRows()[0] as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Yozuvni bekor qilish" }));
    fireEvent.click(within(row).getByRole("button", { name: "Yo'q, qolsin" }));
    expect(within(row).getByRole("button", { name: "Yozuvni bekor qilish" })).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
  });

  it.each([
    ["WOULD_GO_NEGATIVE", "Bekor qilinsa qarz manfiy bo'lib qoladi. Avval keyingi to'lovni bekor qiling."],
    ["ALREADY_REVERSED", "Bu yozuv allaqachon bekor qilingan."],
    ["FORBIDDEN_ROLE", "Bu amal uchun sizning rolingiz yetarli emas."],
  ])("shows the server's refusal %s and changes nothing", async (code, message) => {
    const server = shop(() => refusal(code === "FORBIDDEN_ROLE" ? 403 : 409, code, message));
    await open(server);
    const row = entryRows()[1] as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Yozuvni bekor qilish" }));
    fireEvent.click(within(row).getByRole("button", { name: "Ha, bekor qilinsin" }));
    expect((await screen.findByRole("alert")).textContent).toBe(message);
    expect(screen.getByText(exact("120\u00a0000 so'm"))).toBeTruthy();
    expect(screen.queryByText("Bekor qilingan")).toBeNull();
  });

  it("sends one request for a double tap, and the same key when it is retried", async () => {
    const first = deferred<"offline">();
    const server = shop((_sent, attempt) =>
      attempt === 0 ? first.promise : ok({ entry: {}, customer: customerBody({ balance: 140000 }) }, 201),
    );
    await open(server);
    const row = entryRows()[0] as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Yozuvni bekor qilish" }));
    const confirm = within(row).getByRole("button", { name: "Ha, bekor qilinsin" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(server.writes()).toHaveLength(1);
    expect(within(row).getByRole<HTMLButtonElement>("button", { name: "Saqlanmoqda…" }).disabled).toBe(true);

    first.resolve("offline");
    await screen.findByRole("alert");
    fireEvent.click(within(row).getByRole("button", { name: "Ha, bekor qilinsin" }));
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    expect(server.writes()[1]?.headers["Idempotency-Key"]).toBe(server.writes()[0]?.headers["Idempotency-Key"]);
  });
});

describe("rename and archive", () => {
  it("sends only what changed", async () => {
    const server = shop(() => ok(customerBody({ display_name: "Ali aka" })));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Tahrirlash" }));
    const form = screen.getByRole("form", { name: "Tahrirlash" });
    fireEvent.change(within(form).getByLabelText("Ism"), { target: { value: " Ali  aka " } });
    fireEvent.click(within(form).getByRole("button", { name: "Saqlash" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({ method: "PATCH", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}` });
    expect(server.writes()[0]?.body).toEqual({ display_name: "Ali aka" });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
  });

  it("removes the phone with null and can switch reminders off", async () => {
    const server = shop(() => ok(customerBody({ phone: null, reminders_off: true })));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Tahrirlash" }));
    const form = screen.getByRole("form", { name: "Tahrirlash" });
    fireEvent.change(within(form).getByLabelText("Telefon (ixtiyoriy)"), { target: { value: "" } });
    fireEvent.click(within(form).getByRole("checkbox", { name: "Bu mijozga eslatma yuborilmasin" }));
    fireEvent.click(within(form).getByRole("button", { name: "Saqlash" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toEqual({ phone: null, reminders_off: true });
  });

  it("sends nothing when nothing changed or the name is empty", async () => {
    const server = shop();
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Tahrirlash" }));
    const form = screen.getByRole("form", { name: "Tahrirlash" });
    fireEvent.change(within(form).getByLabelText("Ism"), { target: { value: "  " } });
    fireEvent.click(within(form).getByRole("button", { name: "Saqlash" }));
    expect(within(form).getByRole("alert").textContent).toBe("Mijoz ismini kiriting.");

    fireEvent.change(within(form).getByLabelText("Ism"), { target: { value: "Ali Valiyev" } });
    fireEvent.click(within(form).getByRole("button", { name: "Saqlash" }));
    expect(screen.queryByRole("form", { name: "Tahrirlash" })).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });

  it("shows the server's refusal to archive a customer who still owes", async () => {
    const server = shop(() => refusal(409, "CUSTOMER_HAS_BALANCE", "Qarzi bor mijozni arxivlab bo'lmaydi."));
    await open(server);
    fireEvent.click(screen.getByRole("button", { name: "Arxivlash" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Qarzi bor mijozni arxivlab bo'lmaydi.");
    expect(server.writes()[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}/archive` });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
  });

  it("returns an archived customer from the archive", async () => {
    let archived = true;
    const server = shop(
      () => {
        archived = false;
        return ok(customerBody({ balance: 0 }));
      },
      () => ok(detailBody({ status: archived ? "archived" : "active", balance: 0, entries: [], entries_total: 0 })),
    );
    await open(server, "owner");
    fireEvent.click(screen.getByRole("button", { name: "Arxivdan chiqarish" }));
    expect(await screen.findByRole("button", { name: "Arxivlash" })).toBeTruthy();
    expect(server.writes()[0]?.path).toBe(`${SHOP_BASE}/customers/${CUSTOMER_ID}/unarchive`);
    expect(screen.getByRole("link", { name: "Nasiya yozish" })).toBeTruthy();
  });
});

describe("goods on the customer page (REQ-027, REQ-038)", () => {
  const WITH_GOODS = entryBody({
    id: CREDIT_ID,
    amount: 45500,
    lines: [
      lineBody({ line_no: 1, name: "Guruch", qty: "1.500", unit: "kg", unit_price: 25000, line_total: 37500 }),
      lineBody({ line_no: 2, name: "Non", qty: "2", unit: "dona", unit_price: 4000, line_total: 8000, catalog_item_id: null }),
    ],
  });
  const goodsLinks = () => screen.queryAllByRole("link", { name: "Tovarlarni qo'shish" });
  const withEntries = (...entries: unknown[]) => shop(undefined, () => ok(detailBody({ entries, entries_total: entries.length })));

  it("shows the goods of an entry that has them: name, quantity, unit, price and line total", async () => {
    await open(withEntries(WITH_GOODS, PAYMENT));
    const goods = screen.getByRole("list", { name: "Tovarlar" });
    const lines = within(goods).getAllByRole("listitem").map((line) => line.textContent);
    expect(lines).toEqual(["Guruch37\u00a0500 so'm1,5 kg × 25\u00a0000 so'm", "Non8\u00a0000 so'm2 dona × 4\u00a0000 so'm"]);
    // Only the entry with goods has a goods list.
    expect(screen.getAllByRole("list", { name: "Tovarlar" })).toHaveLength(1);
  });

  it.each(["seller", "manager", "owner"] as const)("offers a %s to add goods to an amount-only sale inside the window", async (role) => {
    await open(shop(), role);
    const links = goodsLinks();
    expect(links).toHaveLength(1);
    expect(links[0]?.getAttribute("href")).toBe(`#/customers/${CUSTOMER_ID}/entries/${CREDIT_ID}/goods`);
    // The link belongs to the credit sale, not to the payment above it.
    const [payment, credit] = entryRows();
    expect(within(credit as HTMLElement).queryByRole("link", { name: "Tovarlarni qo'shish" })).not.toBeNull();
    expect(within(payment as HTMLElement).queryByRole("link", { name: "Tovarlarni qo'shish" })).toBeNull();
  });

  it.each([
    ["has goods", WITH_GOODS],
    ["was reversed", entryBody({ id: CREDIT_ID, reversed: true })],
    ["is a payment", PAYMENT],
    ["is a reversal", entryBody({ id: CREDIT_ID, kind: "reversal", reverses_id: PAYMENT_ID })],
  ])("does not offer it for an entry that %s", async (_what, entry) => {
    await open(withEntries(entry));
    expect(goodsLinks()).toHaveLength(0);
  });

  it("offers it through the end of the day after the sale, Tashkent time, and not a moment later", async () => {
    // CREDIT was sold at 00:30 on 6 October in Tashkent; 7 October ends there at 19:00 UTC.
    renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: shop().fetch, now: new Date("2026-10-07T18:59:59.999Z") });
    await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
    expect(goodsLinks()).toHaveLength(1);
    cleanup();
    renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: shop().fetch, now: new Date("2026-10-07T19:00:00Z") });
    await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
    expect(goodsLinks()).toHaveLength(0);
  });

  it("reads an entry from a server that does not send goods lines yet as an entry without goods", async () => {
    const old: Record<string, unknown> = { ...CREDIT };
    delete old["lines"];
    await open(withEntries(old));
    expect(screen.queryByRole("list", { name: "Tovarlar" })).toBeNull();
    expect(entryRows()).toHaveLength(1);
  });
});

describe("who is offered goods on the customer page", () => {
  const OTHER = "33333333-3333-4333-8333-333333333334";
  const goodsLinks = () => screen.queryAllByRole("link", { name: "Tovarlarni qo'shish" });
  const show = async (role: Role, membershipId: string | null) => {
    const server = shop(undefined, () => ok(detailBody({ entries: [entryBody({ id: CREDIT_ID })], entries_total: 1 })));
    renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: server.fetch, role, membershipId });
    await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
  };

  it("not a seller who did not record the sale", async () => {
    await show("seller", OTHER);
    expect(goodsLinks()).toHaveLength(0);
  });

  it.each(["manager", "owner"] as const)("a %s who did not record the sale", async (role) => {
    await show(role, OTHER);
    expect(goodsLinks()).toHaveLength(1);
  });

  it("a seller when the server does not say which membership is theirs", async () => {
    await show("seller", null);
    expect(goodsLinks()).toHaveLength(1);
  });
});

describe("disputes and the Telegram link on the customer page", () => {
  const disputed = () => ok(detailBody({ entries: [PAYMENT, { ...CREDIT, disputed: true }], entries_total: 2 }));

  it.each(["seller", "manager", "owner"] as const)("shows a %s the disputed mark on the entry", async (role) => {
    await open(shop(undefined, disputed), role);
    const [payment, credit] = entryRows();
    expect(credit?.textContent).toContain("Mijoz e'tiroz bildirgan");
    expect(payment?.textContent).not.toContain("e'tiroz");
  });

  it("leads a manager and an owner to the list of disputes, and a seller nowhere", async () => {
    for (const role of ["manager", "owner"] as const) {
      await open(shop(undefined, disputed), role);
      expect(screen.getByRole("link", { name: "E'tirozlar ro'yxati" }).getAttribute("href")).toBe("#/disputes");
      cleanup();
    }
    await open(shop(undefined, disputed), "seller");
    expect(screen.queryByRole("link", { name: "E'tirozlar ro'yxati" })).toBeNull();
  });

  it.each(["seller", "manager", "owner"] as const)("shows a %s the link state and offers a personal link", async (role) => {
    const server = shop();
    await open(server, role);
    const section = screen.getByRole("region", { name: "Telegramga ulash" });
    expect(await within(section).findByText("Mijoz hali Telegramga ulanmagan.")).toBeTruthy();
    expect(within(section).getByRole("button", { name: "Shaxsiy havola yaratish" })).toBeTruthy();
    expect(server.sent.some((sent) => sent.path === `${SHOP_BASE}/customers/${CUSTOMER_ID}/link`)).toBe(true);
  });

  it("offers no personal link for an archived customer", async () => {
    await open(shop(undefined, () => ok(detailBody({ status: "archived", balance: 0 }))));
    const section = screen.getByRole("region", { name: "Telegramga ulash" });
    await within(section).findByText("Mijoz hali Telegramga ulanmagan.");
    expect(within(section).queryByRole("button")).toBeNull();
  });
});

describe("what the customer page offers by permission", () => {
  const names = () => screen.queryAllByRole("button").map((button) => button.textContent);
  const links = () => screen.queryAllByRole("link").map((link) => link.textContent);

  async function openWith(role: Role, permissions: string[]) {
    renderScreen(<CustomerScreen customerId={CUSTOMER_ID} />, { fetch: shop().fetch, role, permissions });
    await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
  }

  it("offers a seller who was granted it the reversal a seller's role does not give", async () => {
    await openWith("seller", ["ledger.view", "credits.record", "payments.record", "entries.cancel"]);
    expect(reverseButtons().length).toBeGreaterThan(0);
    expect(names()).not.toContain("Tahrirlash");
  });

  it("offers a manager who was denied it no reversal and no editing", async () => {
    await openWith("manager", ["ledger.view", "credits.record", "payments.record"]);
    expect(reverseButtons()).toHaveLength(0);
    expect(names()).not.toContain("Tahrirlash");
    expect(names()).not.toContain("Arxivlash");
  });

  it("offers the sale and the payment separately", async () => {
    await openWith("seller", ["ledger.view", "payments.record"]);
    expect(links()).toContain("To'lov qabul qilish");
    expect(links()).not.toContain("Nasiya yozish");
    cleanup();
    await openWith("seller", ["ledger.view", "credits.record"]);
    expect(links()).toContain("Nasiya yozish");
    expect(links()).not.toContain("To'lov qabul qilish");
    cleanup();
    await openWith("owner", ["ledger.view"]);
    expect(links()).not.toContain("Nasiya yozish");
    expect(links()).not.toContain("To'lov qabul qilish");
  });
});
