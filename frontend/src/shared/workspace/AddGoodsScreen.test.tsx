// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  CUSTOMER_ID,
  deferred,
  detailBody,
  entryBody,
  fakeServer,
  FIVE_ITEMS,
  lineBody,
  ok,
  refusal,
  type Reply,
  type Sent,
  SHOP_BASE,
} from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import type { Entry } from "../api";
import type { Role } from "../navigation";
import { AddGoodsScreen, canAddGoods, mayAddGoods } from "./AddGoodsScreen";

afterEach(cleanup);

const ENTRY_ID = "22222222-2222-4222-8222-222222222222";
const NON = "44444444-4444-4444-8444-444444444441";
const LINES_PATH = `${SHOP_BASE}/entries/${ENTRY_ID}/lines`;

// The sale: 45 000 so'm at 00:30 on Tuesday 6 October 2026 in Tashkent. The tests' clock is noon that day.
const added = () =>
  ok({ entry: { id: ENTRY_ID, amount: 45000, lines: [lineBody({ qty: "1", unit: "kg", name: "Guruch", unit_price: 45000, line_total: 45000 })] } }, 201);

function shop(onWrite: (sent: Sent, attempt: number) => Reply = added, entry: Record<string, unknown> = {}) {
  let attempt = 0;
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      return onWrite(sent, attempt++);
    }
    if (sent.path === `${SHOP_BASE}/catalog`) {
      return ok({ items: FIVE_ITEMS, next_cursor: null });
    }
    return ok(detailBody({ entries: [entryBody(entry)] }));
  });
}

async function open(server: ReturnType<typeof fakeServer>, options: { role?: Role; now?: Date; entryId?: string } = {}) {
  renderScreen(<AddGoodsScreen customerId={CUSTOMER_ID} entryId={options.entryId ?? ENTRY_ID} />, {
    fetch: server.fetch,
    ...(options.role ? { role: options.role } : {}),
    ...(options.now ? { now: options.now } : {}),
  });
  await screen.findByRole("heading", { level: 2, name: "Ali Valiyev" });
}

async function openForm(server: ReturnType<typeof fakeServer>, role?: Role) {
  await open(server, role ? { role } : {});
  await screen.findByRole("list", { name: "Katalogdagi tovarlar" });
}

const type = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });
const pick = (name: string) =>
  fireEvent.click(within(screen.getByRole("list", { name: "Katalogdagi tovarlar" })).getByRole("button", { name: new RegExp(`^${name}`) }));
const qty = (name: string) => screen.getByLabelText<HTMLInputElement>(`${name}: miqdor`);
const price = (name: string) => screen.getByLabelText<HTMLInputElement>(`${name}: narx, so'm`);
const save = () => screen.getByRole<HTMLButtonElement>("button", { name: "Tovarlarni saqlash" });
const difference = () => screen.getByRole("status").textContent;

describe("adding goods to an amount-only sale (REQ-038)", () => {
  it("shows the entry amount and how far the goods are from it, live", async () => {
    const server = shop();
    await openForm(server);
    expect(screen.getByText("Yozuv summasi:").parentElement?.textContent).toBe("Yozuv summasi: 45 000 so'm");
    expect(screen.getByText("2026-yil 6-oktabr, 00:30")).toBeTruthy();
    expect(difference()).toBe("Yana 45 000 so'm yetishmayapti.");
    expect(save().disabled).toBe(true);

    pick("Guruch"); // 25 000
    expect(difference()).toBe("Yana 20 000 so'm yetishmayapti.");
    type(qty("Guruch"), "2");
    expect(difference()).toBe("Tovarlar jami yozuv summasidan 5 000 so'm ko'p.");
    expect(save().disabled).toBe(true);
    type(qty("Guruch"), "1,8");
    expect(difference()).toBe("Summalar teng.");
    expect(save().disabled).toBe(false);
    type(qty("Guruch"), "1,799"); // 44 975
    expect(difference()).toBe("Yana 25 so'm yetishmayapti.");
    expect(save().disabled).toBe(true);
    expect(server.writes()).toHaveLength(0);
  });

  it("saves the lines once the sum equals the amount", async () => {
    const server = shop();
    await openForm(server);
    pick("Guruch");
    type(qty("Guruch"), "1.8");
    fireEvent.click(save());

    const done = await screen.findByText("Tovarlar yozuvga qo'shildi.");
    const write = server.writes()[0];
    expect(write).toMatchObject({ method: "POST", path: LINES_PATH });
    expect(write?.body).toEqual({ lines: [{ catalog_item_id: "44444444-4444-4444-8444-444444444444", qty: "1.8", unit_price: 25000 }] });
    expect(write?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(done.closest("[role=status]")?.textContent).toContain("Guruch");
    expect(screen.getByRole("link", { name: "Mijoz sahifasi" }).getAttribute("href")).toBe(`#/customers/${CUSTOMER_ID}`);
    expect(screen.queryByRole("button", { name: "Tovarlarni saqlash" })).toBeNull();
  });

  it("does not send a sum that differs, even when the form is submitted without the button", async () => {
    const server = shop();
    await openForm(server);
    const form = save().closest("form") as HTMLFormElement;
    fireEvent.submit(form); // no lines at all
    pick("Non"); // 4 000 of 45 000
    fireEvent.click(save());
    fireEvent.submit(form);
    type(price("Non"), "45001");
    fireEvent.submit(form);
    expect(server.writes()).toHaveLength(0);

    type(price("Non"), "45000");
    fireEvent.submit(form);
    await screen.findByText("Tovarlar yozuvga qo'shildi.");
    expect(server.writes()).toHaveLength(1);
  });

  it("does not send while a line cannot be read, though the readable lines already reach the amount", async () => {
    const server = shop();
    await openForm(server);
    pick("Non");
    type(price("Non"), "45000");
    pick("Sut");
    type(qty("Sut"), "");
    expect(difference()).toBe("Summalar teng.");
    expect(save().disabled).toBe(true);
    fireEvent.submit(save().closest("form") as HTMLFormElement);
    expect(server.writes()).toHaveLength(0);
    expect(screen.getByRole("alert").textContent).toBe("Miqdorni kiriting.");
  });

  it.each([
    [409, "LINES_ALREADY_ADDED", "Bu yozuvga tovarlar allaqachon qo'shilgan."],
    [409, "LINES_SUM_MISMATCH", "Tovarlar jami yozuv summasiga teng emas."],
    [409, "LINES_WINDOW_CLOSED", "Tovar qo'shish muddati o'tgan."],
    [403, "FORBIDDEN_ROLE", "Bu amal uchun sizning rolingiz yetarli emas."],
  ])("shows the server's refusal %i %s and keeps the lines", async (status, code, message) => {
    const server = shop(() => refusal(status, code, message));
    await openForm(server, "seller");
    pick("Non");
    type(price("Non"), "45000");
    fireEvent.click(save());
    expect((await screen.findByRole("alert")).textContent).toBe(message);
    expect(price("Non").value).toBe("45000");
    expect(screen.queryByText("Tovarlar yozuvga qo'shildi.")).toBeNull();
  });

  it("sends one request for a double tap and the same key on a retry", async () => {
    const answer = deferred<{ status: number; body: unknown }>();
    const server = shop((_sent, attempt) => (attempt === 0 ? "offline" : answer.promise));
    await openForm(server);
    pick("Non");
    type(price("Non"), "45000");
    fireEvent.click(save());
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Serverga ulanib bo'lmadi.");
    expect(alert.textContent).toContain("Qayta urinsangiz, yozuv ikki marta yozilmaydi.");

    const button = save();
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.submit(button.closest("form") as HTMLFormElement);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Saqlanmoqda…" }).disabled).toBe(true);
    expect(server.writes()).toHaveLength(2);
    answer.resolve(added());
    await screen.findByText("Tovarlar yozuvga qo'shildi.");

    const sent = server.writes();
    expect(sent).toHaveLength(2);
    expect(sent[1]?.headers["Idempotency-Key"]).toBe(sent[0]?.headers["Idempotency-Key"]);
    expect(sent[1]?.body).toEqual(sent[0]?.body);
  });

  it.each([
    ["has goods already", { lines: [lineBody()] }],
    ["was reversed", { reversed: true }],
    ["is a payment", { kind: "payment", promised_date: null }],
    ["is a reversal", { kind: "reversal", reverses_id: "99999999-9999-4999-8999-999999999999" }],
  ])("says goods cannot be added to an entry that %s, and offers no form", async (_what, entry) => {
    const server = shop(added, entry);
    await open(server);
    expect(screen.getByText("Bu yozuvga endi tovar qo'shib bo'lmaydi.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Tovarlarni saqlash" })).toBeNull();
    expect(screen.queryByLabelText("Katalogdan tovar qidirish")).toBeNull();
    expect(server.sent.filter((sent) => sent.path.endsWith("/catalog"))).toHaveLength(0);
  });

  it("is open through the end of the day after the sale and closed from the next instant", async () => {
    // Sold on 6 October in Tashkent: open until 7 October 23:59:59.999 Tashkent, which is 18:59:59.999 UTC.
    const server = shop();
    await open(server, { now: new Date("2026-10-07T18:59:59.999Z") });
    expect(await screen.findByRole("button", { name: "Tovarlarni saqlash" })).toBeTruthy();
    cleanup();
    await open(shop(), { now: new Date("2026-10-07T19:00:00Z") });
    expect(screen.getByText("Bu yozuvga endi tovar qo'shib bo'lmaydi.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Tovarlarni saqlash" })).toBeNull();
  });

  it("shows not found for an entry that is not this customer's, and for a customer that does not exist", async () => {
    const server = shop();
    renderScreen(<AddGoodsScreen customerId={CUSTOMER_ID} entryId="99999999-9999-4999-8999-999999999999" />, { fetch: server.fetch });
    expect(await screen.findByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    cleanup();
    const missing = fakeServer(() => refusal(404, "NOT_FOUND", "Topilmadi."));
    renderScreen(<AddGoodsScreen customerId={CUSTOMER_ID} entryId={ENTRY_ID} />, { fetch: missing.fetch });
    expect(screen.getByRole("status").textContent).toBe("Yuklanmoqda…");
    expect(await screen.findByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
  });

  it("offers a retry when the customer cannot be loaded", async () => {
    let attempt = 0;
    const server = fakeServer((sent) => {
      if (attempt++ === 0) {
        return "offline";
      }
      return sent.path.endsWith("/catalog") ? ok({ items: FIVE_ITEMS, next_cursor: null }) : ok(detailBody());
    });
    renderScreen(<AddGoodsScreen customerId={CUSTOMER_ID} entryId={ENTRY_ID} />, { fetch: server.fetch });
    fireEvent.click(await screen.findByRole("button", { name: "Qayta urinish" }));
    await waitFor(() => expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Ali Valiyev"));
  });
});

describe("which entries are offered goods", () => {
  const entry = (overrides: Partial<Entry> = {}): Entry => ({
    id: ENTRY_ID,
    seq: 1,
    kind: "credit",
    amount: 45000,
    note: null,
    createdAt: "2026-10-05T19:30:00+00:00", // 00:30 on 6 October in Tashkent
    promisedDate: "2026-11-05",
    reversesId: null,
    reversed: false,
    disputed: false,
    authorId: null,
    lines: [],
    ...overrides,
  });
  const NOON = new Date("2026-10-06T07:00:00Z");
  const line = { lineNo: 1, catalogItemId: NON, name: "Non", qty: 1000, unit: "dona", unitPrice: 4000, lineTotal: 4000 };

  it("a credit sale without goods, not reversed, inside the window", () => {
    expect(canAddGoods(entry(), NOON)).toBe(true);
  });

  it.each([
    ["a payment", { kind: "payment" }],
    ["a reversal", { kind: "reversal" }],
    ["an opening balance", { kind: "opening" }],
    ["a reversed sale", { reversed: true }],
    ["a sale that has goods", { lines: [line] }],
    ["a sale with an unreadable time", { createdAt: "yesterday" }],
  ] as [string, Partial<Entry>][])("not %s", (_what, overrides) => {
    expect(canAddGoods(entry(overrides), NOON)).toBe(false);
  });

  it("only until the end of the day after the sale, Tashkent time", () => {
    expect(canAddGoods(entry(), new Date("2026-10-07T18:59:59.999Z"))).toBe(true);
    expect(canAddGoods(entry(), new Date("2026-10-07T19:00:00.000Z"))).toBe(false);
  });

  describe("and to whom", () => {
    const AUTHOR = "33333333-3333-4333-8333-333333333333";
    const OTHER = "33333333-3333-4333-8333-333333333334";
    const mine = entry({ authorId: AUTHOR });

    it("the seller who recorded the entry, whatever the letter case of the identifier", () => {
      expect(mayAddGoods(mine, { role: "seller", membershipId: AUTHOR })).toBe(true);
      expect(mayAddGoods(entry({ authorId: AUTHOR.toUpperCase() }), { role: "seller", membershipId: AUTHOR })).toBe(true);
    });

    it("not another seller", () => {
      expect(mayAddGoods(mine, { role: "seller", membershipId: OTHER })).toBe(false);
    });

    it.each(["manager", "owner"] as const)("a %s, whoever recorded it", (role) => {
      expect(mayAddGoods(mine, { role, membershipId: OTHER })).toBe(true);
    });

    it("anyone when the server does not say who is who: its refusal is then what the seller sees", () => {
      expect(mayAddGoods(mine, { role: "seller", membershipId: null })).toBe(true);
      expect(mayAddGoods(entry({ authorId: null }), { role: "seller", membershipId: OTHER })).toBe(true);
    });
  });
});

describe("a seller who did not record the sale", () => {
  const OTHER = "33333333-3333-4333-8333-333333333334";

  it("is told who can add the goods instead of being given the form, and nothing is sent", async () => {
    const server = fakeServer(() => ok(detailBody()));
    renderScreen(<AddGoodsScreen customerId={CUSTOMER_ID} entryId={ENTRY_ID} />, { fetch: server.fetch, membershipId: OTHER });
    expect(
      await screen.findByText("Tovarlarni yozuvni yozgan xodim, menejer yoki do'kon egasi qo'sha oladi."),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Tovarlarni saqlash" })).toBeNull();
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(server.writes()).toHaveLength(0);
  });

  it.each(["manager", "owner"] as const)("does not stop a %s", async (role) => {
    const server = fakeServer((sent) => (sent.path.endsWith("/catalog") ? ok({ items: [], next_cursor: null }) : ok(detailBody())));
    renderScreen(<AddGoodsScreen customerId={CUSTOMER_ID} entryId={ENTRY_ID} />, { fetch: server.fetch, role, membershipId: OTHER });
    expect(await screen.findByRole("button", { name: "Tovarlarni saqlash" })).toBeTruthy();
  });
});
