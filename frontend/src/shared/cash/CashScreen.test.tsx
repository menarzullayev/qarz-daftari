// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { fakeServer, ok, refusal, type Reply, type Sent, SHOP_BASE } from "../../testing/fakeServer";
import { findHardcodedText } from "../../testing/hardcodedText";
import { renderScreen } from "../../testing/renderScreen";
import { formatMoney } from "../format";
import type { Role } from "../navigation";
import CashScreen from "./CashScreen";
import { CATEGORIES, categoriesBody, category, dayBody, entry, line, summaryBody, total } from "./testing";

afterEach(cleanup);

const CASH = `${SHOP_BASE}/cash`;
/** Text as a person reads it: the no-break spaces inside an amount are spaces. */
const plain = (text: string | null | undefined) => (text ?? "").replace(/\u00a0/g, " ");
/** "500 000 so'm" as the screens write it. */
const som = (amount: string) => `${amount} so'm`;
const dollars = (cents: number) => plain(formatMoney(cents, "uz", "USD"));
const TODAY = "Kassa: 2026-yil 6-oktabr";
const texts = (list: HTMLElement) =>
  within(list)
    .getAllByRole("listitem")
    .map((item) => plain(item.textContent));
/** The element that holds exactly this text, read as a person reads it. */
const findPlain = (text: string) =>
  screen.findByText((_content, element) => element !== null && element.children.length === 0 && plain(element.textContent) === text);

type Routes = {
  day?: (sent: Sent) => Reply;
  summary?: (sent: Sent) => Reply;
  categories?: () => Reply;
  write?: (sent: Sent) => Reply;
};

/** A shop's cash book: the reads answer from `routes`, and every write is recorded. */
function book(routes: Routes = {}) {
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      return routes.write ? routes.write(sent) : ok({ entry: entry() }, 201);
    }
    if (sent.path === `${CASH}/categories`) {
      return routes.categories ? routes.categories() : ok(categoriesBody());
    }
    if (sent.path === `${CASH}/day`) {
      return routes.day ? routes.day(sent) : ok(dayBody());
    }
    if (sent.path === `${CASH}/summary`) {
      return routes.summary ? routes.summary(sent) : ok(summaryBody());
    }
    return refusal(404, "NOT_FOUND", "Topilmadi.");
  });
}

function open(server: ReturnType<typeof fakeServer>, options: { role?: Role; permissions?: string[] } = {}) {
  return renderScreen(<CashScreen />, {
    fetch: server.fetch,
    role: options.role ?? "manager",
    ...(options.permissions ? { permissions: options.permissions } : {}),
  });
}

const button = (name: string) => screen.getByRole("button", { name });
const reads = (server: ReturnType<typeof fakeServer>, path: string) =>
  server.sent.filter((sent) => sent.method === "GET" && sent.path === `${CASH}/${path}`);

describe("the day's book", () => {
  it("shows what each way of paying opened with, took in, paid out and closed with", async () => {
    const server = book();
    open(server);
    expect(await screen.findByRole("heading", { name: TODAY })).toBeTruthy();
    expect(texts(screen.getByRole("list", { name: "Qoldiqlar" }))).toEqual([
      `Naqd${som("300 000")}Boshida: ${som("100 000")} · Kirim: ${som("500 000")} · Chiqim: ${som("300 000")}`,
      `Karta${som("120 000")}Boshida: ${som("0")} · Kirim: ${som("120 000")} · Chiqim: ${som("0")}`,
      `O'tkazma${som("0")}Boshida: ${som("0")} · Kirim: ${som("0")} · Chiqim: ${som("0")}`,
      `Jami${som("420 000")}Boshida: ${som("100 000")} · Kirim: ${som("620 000")} · Chiqim: ${som("300 000")}`,
    ]);
    // Asked for today, with nothing in the address that the server would have to guess.
    expect(reads(server, "day")[0]?.query).toEqual({ date: "2026-10-06" });
  });

  it("lists the entries newest first, income and expense told apart, with the note", async () => {
    open(book());
    const entries = within(await screen.findByRole("list", { name: "Yozuvlar" })).getAllByRole("listitem");
    expect(entries.map((item) => plain(item.querySelector(".row__amount")?.textContent))).toEqual([
      `+${som("120 000")}`,
      `−${som("300 000")}`,
      `+${som("500 000")}`,
    ]);
    expect(entries[1]?.textContent).toContain("Ijara");
    expect(entries[1]?.textContent).toContain("Oktabr uchun");
    expect(entries[0]?.querySelector(".row__amount--in")).not.toBeNull();
    expect(entries[1]?.querySelector(".row__amount--in")).toBeNull();
  });

  it("keeps a cancelled entry in the list, struck through, with its reason and no way to cancel it again", async () => {
    const cancelled = entry({ cancelled: { at: "2026-10-06T08:00:00+00:00", by: "m-2", reason: "Ikki marta yozilgan" } });
    open(book({ day: () => ok(dayBody({ entries: [cancelled] })) }));
    const row = (await screen.findByText("Bekor qilingan: Ikki marta yozilgan")).closest("li");
    expect(row?.className).toBe("row row--struck");
    expect(within(row as HTMLElement).queryByRole("button")).toBeNull();
  });

  it("shows whose payment an entry of the ledger is and sends the reader to the customer to cancel it", async () => {
    const paid = entry({ source: "ledger", customer: { id: "c-1", display_name: "Ali Valiyev" } });
    open(book({ day: () => ok(dayBody({ entries: [paid] })) }));
    const link = await screen.findByRole("link", { name: "Mijoz to'lovi: Ali Valiyev" });
    expect(link.getAttribute("href")).toBe("#/customers/c-1");
    const row = link.closest("li") as HTMLElement;
    expect(within(row).queryByRole("button", { name: "Bekor qilish" })).toBeNull();
    expect(row.textContent).toContain("mijoz sahifasida o'sha to'lovni bekor qiling");
  });

  it("shows money the stock paid out under its category, with no way to cancel it here", async () => {
    const paid = entry({
      source: "stock",
      direction: "expense",
      category: { id: "cat-stock", name: "Ombor: tovar xaridi" },
    });
    open(book({ day: () => ok(dayBody({ entries: [paid, entry({ id: "e-9" })] })) }));
    const row = (await screen.findByText("Ombor: tovar xaridi")).closest("li") as HTMLElement;
    expect(within(row).queryByRole("button", { name: "Bekor qilish" })).toBeNull();
    // The entry written by hand beside it still can be.
    expect(screen.getAllByRole("button", { name: "Bekor qilish" })).toHaveLength(1);
  });

  it("says only that it is a customer's payment to a reader who was not told whose", async () => {
    open(book({ day: () => ok(dayBody({ entries: [entry({ source: "ledger", customer: null })] })) }));
    expect(await screen.findByText("Mijoz to'lovi")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("says that a payment's entry was cancelled by the ledger when it carries no reason", async () => {
    const reversed = entry({
      source: "ledger",
      customer: null,
      cancelled: { at: "2026-10-06T08:00:00+00:00", by: "m-2", reason: null },
    });
    open(book({ day: () => ok(dayBody({ entries: [reversed] })) }));
    expect(await screen.findByText("Bekor qilingan: mijozning to'lovi bekor qilindi.")).toBeTruthy();
  });

  it("reads another day when one is chosen, and refuses a day that is ahead before asking", async () => {
    const server = book({ day: (sent) => ok(dayBody({ date: sent.query["date"], entries: [] })) });
    open(server);
    await screen.findByRole("heading", { name: TODAY });
    fireEvent.change(screen.getByLabelText("Kun"), { target: { value: "2026-10-01" } });
    expect(await screen.findByRole("heading", { name: "Kassa: 2026-yil 1-oktabr" })).toBeTruthy();
    expect(screen.getByText("Bu kunda yozuv yo'q.")).toBeTruthy();
    const asked = reads(server, "day").length;
    fireEvent.change(screen.getByLabelText("Kun"), { target: { value: "2026-10-07" } });
    expect(screen.getByText("Bugundan keyingi kunni ko'rib bo'lmaydi.")).toBeTruthy();
    expect(reads(server, "day")).toHaveLength(asked);
  });

  it("reads the next page of a long day when asked", async () => {
    const server = book({
      day: (sent) =>
        sent.query["cursor"] === "next-1"
          ? ok(dayBody({ entries: [entry({ id: "e-9", amount: 7000 })] }))
          : ok(dayBody({ entries: [entry()], next_cursor: "next-1" })),
    });
    open(server);
    fireEvent.click(await screen.findByRole("button", { name: "Yana ko'rsatish" }));
    await findPlain(`+${som("7 000")}`);
    expect(within(screen.getByRole("list", { name: "Yozuvlar" })).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Yana ko'rsatish" })).toBeNull();
  });

  it("shows each currency in a table of its own and says that they are never added", async () => {
    const twoBooks = dayBody({
      balances: [
        line("cash", { income: 500000, closing: 500000, count: 1 }),
        line("card"),
        line("transfer"),
        line("cash", { currency: "USD", income: 125050, closing: 125050, count: 1 }),
        line("card", { currency: "USD" }),
        line("transfer", { currency: "USD" }),
      ],
      totals: [
        total({ income: 500000, closing: 500000, count: 1 }),
        total({ currency: "USD", income: 125050, closing: 125050, count: 1 }),
      ],
      entries: [entry({ currency: "USD", amount: 125050, method: "cash" })],
    });
    open(book({ day: () => ok(twoBooks), categories: () => ok(categoriesBody({ currencies: ["UZS", "USD"] })) }));
    expect(texts(await screen.findByRole("list", { name: "Qoldiqlar, dollar" }))[3]).toContain(`Jami${dollars(125050)}`);
    expect(texts(screen.getByRole("list", { name: "Qoldiqlar, so'm" }))[3]).toContain(`Jami${som("500 000")}`);
    expect(screen.getByText(/ular hech qachon bir-biriga qo'shilmaydi/)).toBeTruthy();
    // Nowhere on the screen is a figure of the two together.
    expect(document.body.textContent).not.toContain("625");
  });
});

describe("recording an entry", () => {
  async function incomeForm(server = book()) {
    open(server);
    fireEvent.click(await screen.findByRole("button", { name: "Kirim yozish" }));
    return server;
  }

  it("sends an income with its method and category, shows that it was saved and reads the day again", async () => {
    const server = await incomeForm(
      book({ write: () => ok({ entry: entry({ amount: 45000, method: "transfer" }) }, 201) }),
    );
    fireEvent.change(screen.getByLabelText("Summa"), { target: { value: "45 000" } });
    fireEvent.change(screen.getByLabelText("To'lov usuli"), { target: { value: "transfer" } });
    fireEvent.change(screen.getByLabelText("Toifa"), { target: { value: "cat-income-Savdo" } });
    fireEvent.change(screen.getByLabelText("Izoh (ixtiyoriy)"), { target: { value: "  Kunlik   savdo " } });
    const before = reads(server, "day").length;
    fireEvent.click(button("Kirimni saqlash"));
    expect(await findPlain(`Kirim yozildi: ${som("45 000")}.`)).toBeTruthy();
    const [sent] = server.writes();
    expect(sent).toMatchObject({ method: "POST", path: `${CASH}/entries` });
    expect(sent?.body).toEqual({
      direction: "income",
      method: "transfer",
      amount: 45000,
      category_id: "cat-income-Savdo",
      note: "Kunlik savdo",
    });
    expect(sent?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    await waitFor(() => expect(reads(server, "day").length).toBeGreaterThan(before));
    expect(screen.queryByRole("form", { name: "Yangi kirim" })).toBeNull();
  });

  it("offers the categories of the direction that still take entries, and never the ledger's own", async () => {
    const withArchived = [...CATEGORIES, category("income", "Eski", { archived: true })];
    await incomeForm(book({ categories: () => ok(categoriesBody({ items: withArchived })) }));
    const options = within(screen.getByLabelText("Toifa")).getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual(["Toifani tanlang", "Savdo"]);
  });

  it("sends nothing while the amount or the category is missing, and says which", async () => {
    const two = [...CATEGORIES, category("income", "Boshqa kirim")];
    const server = await incomeForm(book({ categories: () => ok(categoriesBody({ items: two })) }));
    fireEvent.click(button("Kirimni saqlash"));
    expect(screen.getByText("Summani kiriting.")).toBeTruthy();
    expect(screen.getByText("Toifani tanlang.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Summa"), { target: { value: "50" } });
    fireEvent.click(button("Kirimni saqlash"));
    expect(screen.getByText(/Summa kamida/)).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
  });

  it("dates an entry on a chosen day of the last month and refuses an older one before sending", async () => {
    const server = await incomeForm();
    fireEvent.change(screen.getByLabelText("Summa"), { target: { value: "45000" } });
    fireEvent.change(screen.getByLabelText("Qaysi kunga"), { target: { value: "2026-09-01" } });
    fireEvent.click(button("Kirimni saqlash"));
    expect(screen.getByText("Bu kun juda eski: faqat oxirgi 31 kun ichida yozish mumkin.")).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Qaysi kunga"), { target: { value: "2026-10-01" } });
    fireEvent.click(button("Kirimni saqlash"));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toMatchObject({ day: "2026-10-01" });
  });

  it("puts the server's refusal of a category next to the category and keeps the form", async () => {
    const server = await incomeForm(
      book({ write: () => refusal(409, "CASH_CATEGORY_ARCHIVED", "Bu toifa arxivda. Boshqa toifani tanlang.") }),
    );
    fireEvent.change(screen.getByLabelText("Summa"), { target: { value: "45000" } });
    fireEvent.click(button("Kirimni saqlash"));
    expect(await screen.findByText("Bu toifa arxivda. Boshqa toifani tanlang.")).toBeTruthy();
    expect(screen.getByLabelText("Toifa").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByRole("form", { name: "Yangi kirim" })).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
  });

  it("offers a currency only in a shop that works in two, and sends dollars in cents", async () => {
    const server = await incomeForm(book({ categories: () => ok(categoriesBody({ currencies: ["UZS", "USD"] })) }));
    fireEvent.change(screen.getByLabelText("Valyuta"), { target: { value: "USD" } });
    fireEvent.change(screen.getByLabelText("Summa"), { target: { value: "1250.5" } });
    fireEvent.click(button("Kirimni saqlash"));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]?.body).toMatchObject({ currency: "USD", amount: 125050 });
  });

  it("offers no currency in a shop that works in so'm alone", async () => {
    await incomeForm();
    expect(screen.queryByLabelText("Valyuta")).toBeNull();
  });

  it("says that a category is needed first when the direction has none", async () => {
    const server = book({ categories: () => ok(categoriesBody({ items: [category("income", "Savdo")] })) });
    open(server);
    fireEvent.click(await screen.findByRole("button", { name: "Chiqim yozish" }));
    expect(screen.getByText(/Bu yo'nalishda toifa yo'q/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Chiqimni saqlash" })).toBeNull();
  });
});

describe("cancelling an entry", () => {
  it("asks for a reason, sends it, and reads the day again", async () => {
    const server = book();
    open(server);
    const row = (await screen.findByText("Oktabr uchun")).closest("li") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Bekor qilish" }));
    // Too short a reason is not sent.
    fireEvent.change(within(row).getByLabelText("Bekor qilish sababi"), { target: { value: "x" } });
    fireEvent.click(within(row).getByRole("button", { name: "Yozuvni bekor qilish" }));
    expect(server.writes()).toHaveLength(0);
    fireEvent.change(within(row).getByLabelText("Bekor qilish sababi"), { target: { value: "  Ikki marta   yozilgan " } });
    const before = reads(server, "day").length;
    fireEvent.click(within(row).getByRole("button", { name: "Yozuvni bekor qilish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({
      method: "POST",
      path: `${CASH}/entries/e-2/cancellation`,
      body: { reason: "Ikki marta yozilgan" },
    });
    await waitFor(() => expect(reads(server, "day").length).toBeGreaterThan(before));
  });

  it("shows the server's refusal and leaves the entry as it was", async () => {
    const server = book({ write: () => refusal(409, "CASH_ENTRY_CANCELLED", "Bu kassa yozuvi allaqachon bekor qilingan.") });
    open(server);
    const row = (await screen.findByText("Oktabr uchun")).closest("li") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Bekor qilish" }));
    fireEvent.change(within(row).getByLabelText("Bekor qilish sababi"), { target: { value: "Xato yozilgan" } });
    fireEvent.click(within(row).getByRole("button", { name: "Yozuvni bekor qilish" }));
    expect(await within(row).findByText("Bu kassa yozuvi allaqachon bekor qilingan.")).toBeTruthy();
    expect(row.className).toBe("row");
  });
});

describe("who is offered what", () => {
  it("shows a seller nothing and asks the server nothing", async () => {
    const server = book();
    open(server, { role: "seller" });
    expect(await screen.findByText("Bunday sahifa yo'q yoki sizda unga ruxsat yo'q.")).toBeTruthy();
    expect(server.sent).toHaveLength(0);
  });

  it("lets a member who may only record income record it, without the book and without expense", async () => {
    const server = book();
    open(server, { role: "seller", permissions: ["cash.record_income"] });
    expect(await screen.findByRole("button", { name: "Kirim yozish" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Chiqim yozish" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Bo'lim" })).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 20));
    // The book is not theirs to read: it is not asked for.
    expect(reads(server, "day")).toHaveLength(0);
    expect(reads(server, "summary")).toHaveLength(0);
  });

  it("lets a member who may read but not record or cancel only read", async () => {
    open(book(), { role: "seller", permissions: ["cash.view"] });
    await screen.findByRole("heading", { name: TODAY });
    expect(screen.queryByRole("button", { name: "Kirim yozish" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Chiqim yozish" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Bekor qilish" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Toifalar" })).toBeNull();
  });

  it("shows the failure, with a way to try again, when the cash book cannot be read", async () => {
    let attempts = 0;
    const server = book({
      categories: () => (attempts++ === 0 ? refusal(503, "TIMEOUT", "") : ok(categoriesBody())),
    });
    open(server);
    fireEvent.click(await screen.findByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByRole("heading", { name: TODAY })).toBeTruthy();
  });
});

describe("a period", () => {
  async function summaryView(server = book()) {
    open(server);
    fireEvent.click(await screen.findByRole("button", { name: "Davr hisoboti" }));
    await screen.findByRole("heading", { name: "Toifalar bo'yicha" });
    return server;
  }

  it("shows the month so far by category with each one's share, and by day", async () => {
    const server = await summaryView();
    expect(reads(server, "summary")[0]?.query).toEqual({ from: "2026-10-01", to: "2026-10-06" });
    expect(texts(screen.getByRole("list", { name: "Kirim" }))).toEqual([
      `Savdo${som("700 000")}2 ta yozuv · 100%`,
      `Jami${som("700 000")}`,
    ]);
    expect(texts(screen.getByRole("list", { name: "Chiqim" }))[0]).toBe(`Ijara${som("300 000")}1 ta yozuv · 100%`);
    expect(texts(screen.getByRole("list", { name: "Kunlar bo'yicha" }))[0]).toBe(
      `2026-yil 5-oktabrKirim: ${som("700 000")} · Chiqim: ${som("300 000")}`,
    );
    expect(texts(screen.getByRole("list", { name: "Qoldiqlar" }))[0]).toContain(`Boshida: ${som("100 000")}`);
  });

  it("asks again for a chosen period and refuses one that ends ahead before asking", async () => {
    const server = await summaryView();
    fireEvent.click(button("Bugun"));
    await waitFor(() => expect(reads(server, "summary").at(-1)?.query).toEqual({ from: "2026-10-06", to: "2026-10-06" }));
    const asked = reads(server, "summary").length;
    fireEvent.change(screen.getByLabelText("Tugash sanasi"), { target: { value: "2026-12-01" } });
    fireEvent.click(button("Ko'rsatish"));
    expect(screen.getByText("Davr bugundan keyin tugashi mumkin emas.")).toBeTruthy();
    expect(reads(server, "summary")).toHaveLength(asked);
  });

  it("says so when nothing stands in the period, and marks a category that was since archived", async () => {
    await summaryView(book({ summary: () => ok(summaryBody({ categories: [], days: [] })) }));
    expect(screen.getByText("Bu davrda yozuv yo'q.")).toBeTruthy();
    cleanup();
    const archived = { category: category("expense", "Ijara", { archived: true }), currency: "UZS", amount: 300000, count: 1 };
    await summaryView(book({ summary: () => ok(summaryBody({ categories: [archived] })) }));
    expect(screen.getByText("Ijara (arxivda)")).toBeTruthy();
  });

  const LINK = { from: "2026-10-01", to: "2026-10-06", entries: 3, url: "/files/abc.def", expires_at: "2026-10-06T07:05:00+00:00" };
  const exports = (server: ReturnType<typeof fakeServer>) => server.sent.filter((sent) => sent.path === `${CASH}/export`);

  it("writes the period on the screen to a file and offers the link, asking for nothing until told to", async () => {
    const server = await summaryView(book({ write: () => ok(LINK, 201) }));
    expect(exports(server)).toHaveLength(0);
    fireEvent.click(button("Davrni faylga chiqarish"));
    const link = await screen.findByRole("link", { name: "Faylni yuklab olish" });
    expect(link.getAttribute("href")).toBe("/files/abc.def");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(exports(server)).toHaveLength(1);
    expect(exports(server)[0]).toMatchObject({ method: "POST", body: { from: "2026-10-01", to: "2026-10-06" } });
    expect(exports(server)[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    // The link is a credential: it is in neither the address nor the browser's storage.
    expect(window.location.href).not.toContain("abc.def");
    expect(JSON.stringify({ ...window.localStorage, ...window.sessionStorage })).not.toContain("abc.def");
    expect(button("Yangi havola olish")).toBeTruthy();
  });

  it("exports the period that was chosen, and forgets the link of the one before", async () => {
    const server = await summaryView(book({ write: () => ok(LINK, 201) }));
    fireEvent.click(button("Davrni faylga chiqarish"));
    await screen.findByRole("link", { name: "Faylni yuklab olish" });
    fireEvent.click(button("Bugun"));
    await waitFor(() => expect(screen.queryByRole("link", { name: "Faylni yuklab olish" })).toBeNull());
    fireEvent.click(button("Davrni faylga chiqarish"));
    await screen.findByRole("link", { name: "Faylni yuklab olish" });
    expect(exports(server).map((sent) => sent.body)).toEqual([
      { from: "2026-10-01", to: "2026-10-06" },
      { from: "2026-10-06", to: "2026-10-06" },
    ]);
  });

  it("says that a period holds too many entries for one file, and shows any other refusal as it is", async () => {
    let reply: Reply = refusal(422, "VALIDATION", "Ma'lumot noto'g'ri.", { to: "TOO_MANY_ENTRIES" });
    await summaryView(book({ write: () => reply }));
    fireEvent.click(button("Davrni faylga chiqarish"));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Bu davrda yozuvlar juda ko'p: bitta faylga sig'maydi. Qisqaroq davrni tanlang.",
    );
    expect(screen.queryByRole("link", { name: "Faylni yuklab olish" })).toBeNull();
    reply = refusal(503, "FILE_STORE_UNAVAILABLE", "Fayllarni saqlash hozir ishlamayapti.");
    fireEvent.click(button("Davrni faylga chiqarish"));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Fayllarni saqlash hozir ishlamayapti."));
  });

  it("is offered by the permission to read the book, whatever the role", async () => {
    // A seller who was given the right to read the book is offered its file too.
    const granted = book({ write: () => ok(LINK, 201) });
    open(granted, { role: "seller", permissions: ["cash.view"] });
    fireEvent.click(await screen.findByRole("button", { name: "Davr hisoboti" }));
    expect(await screen.findByRole("button", { name: "Davrni faylga chiqarish" })).toBeTruthy();
    cleanup();
    // A manager from whom it was taken has no period report at all, so nothing to export.
    const denied = book();
    open(denied, { role: "manager", permissions: ["cash.record_income"] });
    await screen.findByRole("button", { name: "Kirim yozish" });
    expect(screen.queryByRole("button", { name: "Davr hisoboti" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Davrni faylga chiqarish" })).toBeNull();
    expect(exports(denied)).toHaveLength(0);
  });

  it("keeps the categories of each currency apart, each with its own total", async () => {
    const two = summaryBody({
      totals: [total({ income: 700000, closing: 700000, count: 2 }), total({ currency: "USD", income: 125050, closing: 125050, count: 1 })],
      balances: [line("cash", { income: 700000, closing: 700000, count: 2 }), line("cash", { currency: "USD", income: 125050, closing: 125050, count: 1 })],
      categories: [
        { category: category("income", "Savdo"), currency: "UZS", amount: 700000, count: 2 },
        { category: category("income", "Savdo"), currency: "USD", amount: 125050, count: 1 },
      ],
      days: [],
    });
    await summaryView(book({ summary: () => ok(two) }));
    expect(texts(screen.getByRole("list", { name: "Kirim, so'm" }))[1]).toBe(`Jami${som("700 000")}`);
    expect(texts(screen.getByRole("list", { name: "Kirim, dollar" }))[1]).toBe(`Jami${dollars(125050)}`);
  });
});

describe("the categories", () => {
  async function categoriesView(server = book(), options: { role?: Role } = {}) {
    open(server, options);
    fireEvent.click(await screen.findByRole("button", { name: "Toifalar" }));
    await screen.findByRole("list", { name: "Chiqim toifalari" });
    return server;
  }

  it("adds a category of the chosen direction and reads the list again", async () => {
    const server = await categoriesView(book({ write: () => ok(category("income", "Ijara haqi"), 201) }));
    fireEvent.change(screen.getByLabelText("Yo'nalish"), { target: { value: "income" } });
    fireEvent.change(screen.getByLabelText("Toifa nomi"), { target: { value: "  Ijara   haqi " } });
    const before = reads(server, "categories").length;
    fireEvent.click(screen.getAllByRole("button", { name: "Toifa qo'shish" })[0] as HTMLElement);
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    expect(server.writes()[0]).toMatchObject({
      method: "POST",
      path: `${CASH}/categories`,
      body: { direction: "income", name: "Ijara haqi" },
    });
    await waitFor(() => expect(reads(server, "categories").length).toBeGreaterThan(before));
  });

  it("sends no name that is empty, and shows the server's refusal of a name that is taken", async () => {
    const server = await categoriesView(
      book({ write: () => refusal(409, "CASH_CATEGORY_NAME_TAKEN", "Shu nomli toifa allaqachon bor.") }),
    );
    fireEvent.click(screen.getAllByRole("button", { name: "Toifa qo'shish" })[0] as HTMLElement);
    expect(screen.getByText(/Nom 1 dan 60 belgigacha/)).toBeTruthy();
    expect(server.writes()).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Toifa nomi"), { target: { value: "Ijara" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Toifa qo'shish" })[0] as HTMLElement);
    expect(await screen.findByText("Shu nomli toifa allaqachon bor.")).toBeTruthy();
  });

  it("renames, archives and deletes a category, the last only after a question", async () => {
    const server = await categoriesView(
      book({ write: (sent) => (sent.method === "DELETE" ? ok({ deleted: true }) : ok(category("expense", "Ijara"))) }),
    );
    const row = () => screen.getByText("Transport").closest("li") as HTMLElement;
    fireEvent.click(within(row()).getByRole("button", { name: "Nomini o'zgartirish" }));
    fireEvent.change(within(row()).getByLabelText("«Transport» nomini o'zgartirish"), { target: { value: "Yo'l xarajati" } });
    fireEvent.click(within(row()).getByRole("button", { name: "Saqlash" }));
    await waitFor(() => expect(server.writes()).toHaveLength(1));
    await waitFor(() => expect(within(row()).queryByRole("button", { name: "Saqlash" })).toBeNull());
    fireEvent.click(within(row()).getByRole("button", { name: "Arxivlash" }));
    await waitFor(() => expect(server.writes()).toHaveLength(2));
    fireEvent.click(within(row()).getByRole("button", { name: "O'chirish" }));
    // Nothing is sent until the question is answered.
    expect(server.writes()).toHaveLength(2);
    fireEvent.click(within(row()).getByRole("button", { name: "Ha, o'chirish" }));
    await waitFor(() => expect(server.writes()).toHaveLength(3));
    expect(server.writes().map((sent) => [sent.method, sent.path.replace(CASH, ""), sent.body])).toEqual([
      ["PATCH", "/categories/cat-expense-Transport", { name: "Yo'l xarajati" }],
      ["PATCH", "/categories/cat-expense-Transport", { archived: true }],
      ["DELETE", "/categories/cat-expense-Transport", undefined],
    ]);
  });

  it("offers to bring an archived category back, and only to rename the one payments land in", async () => {
    const items = [category("income", "Qarz qaytdi", { fixed: true }), category("expense", "Eski", { archived: true })];
    await categoriesView(book({ categories: () => ok(categoriesBody({ items })) }));
    const fixed = screen.getByText("Qarz qaytdi").closest("li") as HTMLElement;
    expect(within(fixed).getAllByRole("button").map((control) => control.textContent)).toEqual(["Nomini o'zgartirish"]);
    expect(fixed.textContent).toContain("Mijozlar to'lovi shu toifaga tushadi");
    const archived = screen.getByText("Eski").closest("li") as HTMLElement;
    expect(within(archived).getByText("arxivda")).toBeTruthy();
    expect(within(archived).getByRole("button", { name: "Arxivdan chiqarish" })).toBeTruthy();
  });

  it("offers the owner alone to copy past payments in, and only after a question", async () => {
    await categoriesView(book(), { role: "manager" });
    expect(screen.queryByRole("heading", { name: "Avvalgi to'lovlarni kassaga ko'chirish" })).toBeNull();
    cleanup();
    const server = await categoriesView(book({ write: () => ok({ written: 12 }) }), { role: "owner" });
    fireEvent.change(screen.getByLabelText("Shu kundan boshlab (ixtiyoriy)"), { target: { value: "2026-09-01" } });
    fireEvent.click(button("To'lovlarni ko'chirish"));
    expect(server.writes()).toHaveLength(0);
    fireEvent.click(button("Ha, ko'chirish"));
    expect(await screen.findByText("12 ta to'lov kassaga ko'chirildi.")).toBeTruthy();
    expect(server.writes()[0]).toMatchObject({ path: `${CASH}/backfill`, body: { since: "2026-09-01" } });
  });

  it("says that nothing was left to copy when the server wrote nothing", async () => {
    await categoriesView(book({ write: () => ok({ written: 0 }) }), { role: "owner" });
    fireEvent.click(button("To'lovlarni ko'chirish"));
    fireEvent.click(button("Ha, ko'chirish"));
    expect(await screen.findByText(/hammasi allaqachon kassada/)).toBeTruthy();
  });
});

describe("the text of the cash screens", () => {
  it.each(["Balances", "CashEntryForm", "CashScreen", "Categories", "DayBook", "Summary"])(
    "%s.tsx has no text written into it: every word comes from the catalog",
    (name) => {
      const file = `${name}.tsx`;
      const source = readFileSync(resolve(import.meta.dirname, file), "utf8");
      expect(findHardcodedText(file, source)).toEqual([]);
    },
  );
});
