// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  CUSTOMER_ID,
  customerBody,
  deferred,
  detailBody,
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
import type { CatalogItem, EntryKind } from "../api";
import { EntryScreen } from "./EntryScreen";
import { type DraftLine, GoodsEditor, pickItem, readDraft, readDrafts } from "./GoodsEditor";

afterEach(cleanup);

const ENTRY_ID = "77777777-7777-4777-8777-777777777777";
const NON = "44444444-4444-4444-8444-444444444441";
const GURUCH = "44444444-4444-4444-8444-444444444444";

const recorded = (amount: number, lines: unknown[] = [], promisedDate = "2026-11-05", kind = "credit") =>
  ok({ entry: { id: ENTRY_ID, kind, amount, promised_date: promisedDate, lines }, customer: customerBody({ balance: 120000 + amount }) }, 201);

/** A shop with five goods and one customer; `onWrite` answers every write. */
function shop(onWrite: (sent: Sent, attempt: number) => Reply, items: unknown[] = FIVE_ITEMS) {
  let attempt = 0;
  return fakeServer((sent) => {
    if (sent.method !== "GET") {
      return onWrite(sent, attempt++);
    }
    if (sent.path === `${SHOP_BASE}/catalog`) {
      const q = (sent.query["q"] ?? "").toLowerCase();
      return ok({ items: items.filter((item) => (item as { name: string }).name.toLowerCase().includes(q)), next_cursor: null });
    }
    return ok(detailBody());
  });
}

async function openForm(server: ReturnType<typeof fakeServer>, kind: EntryKind = "credit") {
  renderScreen(<EntryScreen customerId={CUSTOMER_ID} kind={kind} />, { fetch: server.fetch });
  await screen.findByLabelText("Summa, so'm");
}

async function openGoods(server: ReturnType<typeof fakeServer>) {
  await openForm(server);
  fireEvent.click(screen.getByRole("button", { name: "Tovarlar bilan yozish" }));
  await screen.findByRole("list", { name: "Katalogdagi tovarlar" });
}

const type = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });
const pick = (name: string) =>
  fireEvent.click(within(screen.getByRole("list", { name: "Katalogdagi tovarlar" })).getByRole("button", { name: new RegExp(`^${name}`) }));
const qty = (name: string) => screen.getByLabelText<HTMLInputElement>(`${name}: miqdor`);
const price = (name: string) => screen.getByLabelText<HTMLInputElement>(`${name}: narx, so'm`);
const lineText = (name: string) => qty(name).closest("li")?.textContent ?? "";
const sum = () => screen.getByText("Jami:").parentElement?.textContent;
const submit = () => screen.getByRole<HTMLButtonElement>("button", { name: "Nasiyani yozish" });
const lineRows = () => within(screen.getByRole("list", { name: "Tanlangan tovarlar" })).getAllByRole("listitem");
const writes = (server: ReturnType<typeof fakeServer>) => server.writes().filter((sent) => sent.path.endsWith("/entries"));

describe("itemized credit sale (REQ-037)", () => {
  it("asks the catalog only when goods are wanted", async () => {
    const server = shop(() => recorded(45000));
    await openForm(server);
    expect(server.sent.filter((sent) => sent.path.endsWith("/catalog"))).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Tovarlar bilan yozish" }));
    await screen.findByRole("list", { name: "Katalogdagi tovarlar" });
    const read = server.sent.find((sent) => sent.path.endsWith("/catalog"));
    expect(read).toMatchObject({ method: "GET", path: `${SHOP_BASE}/catalog`, query: { status: "active", limit: "20" } });
  });

  it("takes the catalog price for a picked good, shows the line total and the sum, and sends lines without an amount", async () => {
    const server = shop(() => recorded(4000, [lineBody({ qty: "1", line_total: 4000 })]));
    await openGoods(server);
    pick("Non");

    expect(qty("Non").value).toBe("1");
    expect(price("Non").value).toBe("4000");
    expect(lineText("Non")).toContain("4 000 so'm");
    expect(sum()).toBe("Jami: 4 000 so'm");
    // With goods the amount is their sum: there is no amount field to disagree with it.
    expect(screen.queryByLabelText("Summa, so'm")).toBeNull();

    fireEvent.click(submit());
    const done = await screen.findByRole("status");
    const write = writes(server)[0];
    expect(write).toMatchObject({ method: "POST", path: `${SHOP_BASE}/customers/${CUSTOMER_ID}/entries` });
    expect(write?.body).toEqual({ kind: "credit", lines: [{ catalog_item_id: NON, qty: "1", unit_price: 4000 }] });
    expect(write?.body).not.toHaveProperty("amount");
    expect(write?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(done.textContent).toContain("Ali Valiyev: 4 000 so'm nasiya yozildi.");
    expect(within(done).getByRole("list", { name: "Tovarlar" }).textContent).toContain("Non");
  });

  it("records five catalog goods with one tap each and one to save (REQ-N02)", async () => {
    const server = shop(() => recorded(56500));
    await openGoods(server);
    for (const name of ["Non", "Sut", "Shakar", "Guruch", "Tuxum"]) {
      pick(name);
    }
    expect(lineRows()).toHaveLength(5);
    expect(sum()).toBe("Jami: 56 500 so'm");
    fireEvent.click(submit());
    await screen.findByRole("status");
    const body = writes(server)[0]?.body as { lines: { qty: string; unit_price: number }[] };
    expect(body.lines).toHaveLength(5);
    expect(body.lines.map((line) => line.unit_price)).toEqual([4000, 12000, 14000, 25000, 1500]);
    expect(body.lines.every((line) => line.qty === "1")).toBe(true);
  });

  it("adds one more of a good that is picked again instead of a second line", async () => {
    const server = shop(() => recorded(12000));
    await openGoods(server);
    pick("Non");
    pick("Non");
    pick("Non");
    expect(lineRows()).toHaveLength(1);
    expect(qty("Non").value).toBe("3");
    expect(sum()).toBe("Jami: 12 000 so'm");
  });

  it("reads a quantity with up to three decimals and rounds a half of a so'm up", async () => {
    const server = shop(() => recorded(37500), [...FIVE_ITEMS, { ...(FIVE_ITEMS[0] as object), id: "44444444-4444-4444-8444-444444444449", name: "Qalampir", unit: "kg", price: 4 }]);
    await openGoods(server);
    pick("Guruch");
    type(qty("Guruch"), "1,5");
    expect(lineText("Guruch")).toContain("37 500 so'm");

    pick("Qalampir");
    type(qty("Qalampir"), "0,125"); // 0.5 so'm
    expect(lineText("Qalampir")).toContain("1 so'm");
    type(qty("Qalampir"), "0.375"); // 1.5 so'm, not rounded to even
    expect(lineText("Qalampir")).toContain("2 so'm");
    type(qty("Qalampir"), "0,625"); // 2.5 so'm
    expect(lineText("Qalampir")).toContain("3 so'm");
    expect(sum()).toBe("Jami: 37 503 so'm");

    fireEvent.click(submit());
    await screen.findByRole("status");
    expect(writes(server)[0]?.body).toEqual({
      kind: "credit",
      lines: [
        { catalog_item_id: GURUCH, qty: "1.5", unit_price: 25000 },
        { catalog_item_id: "44444444-4444-4444-8444-444444444449", qty: "0.625", unit_price: 4 },
      ],
    });
  });

  it("lets the price be changed for this line only", async () => {
    const server = shop(() => recorded(7000));
    await openGoods(server);
    pick("Non");
    type(price("Non"), "3500");
    type(qty("Non"), "2");
    expect(lineText("Non")).toContain("7 000 so'm");
    fireEvent.click(submit());
    await screen.findByRole("status");
    expect(writes(server)[0]?.body).toEqual({ kind: "credit", lines: [{ catalog_item_id: NON, qty: "2", unit_price: 3500 }] });
    // The catalog was only read: a line's price never rewrites the catalog price.
    expect(server.writes()).toHaveLength(1);
  });

  it("takes a good typed by name, with and without a unit", async () => {
    const server = shop(() => recorded(23000));
    await openGoods(server);
    type(screen.getByLabelText("Tovar nomi"), "  Qora   choy ");
    type(screen.getByLabelText("Birligi (ixtiyoriy)"), "quti");
    type(screen.getByLabelText("Narxi, so'm"), "18 ming");
    fireEvent.click(screen.getByRole("button", { name: "Ro'yxatga qo'shish" }));
    type(screen.getByLabelText("Tovar nomi"), "Gugurt");
    type(screen.getByLabelText("Narxi, so'm"), "500");
    fireEvent.click(screen.getByRole("button", { name: "Ro'yxatga qo'shish" }));
    type(qty("Gugurt"), "10");

    expect(lineText("Qora choy")).toContain("18 000 so'm");
    expect(sum()).toBe("Jami: 23 000 so'm");
    expect(screen.getByLabelText<HTMLInputElement>("Tovar nomi").value).toBe("");
    fireEvent.click(submit());
    await screen.findByRole("status");
    expect(writes(server)[0]?.body).toEqual({
      kind: "credit",
      lines: [
        { name: "Qora choy", unit: "quti", qty: "1", unit_price: 18000 },
        { name: "Gugurt", qty: "10", unit_price: 500 },
      ],
    });
  });

  it("refuses a typed good without a name or with an unusable price", async () => {
    const server = shop(() => recorded(1000));
    await openGoods(server);
    fireEvent.click(screen.getByRole("button", { name: "Ro'yxatga qo'shish" }));
    expect(screen.getAllByRole("alert").map((alert) => alert.textContent)).toEqual(["Mahsulot nomini kiriting.", "Narxni kiriting."]);
    type(screen.getByLabelText("Tovar nomi"), "Choy");
    for (const [typed, message] of [
      ["0", "Narx 1 so'mdan 100 000 000 so'mgacha bo'lishi kerak."],
      ["100000001", "Narx 1 so'mdan 100 000 000 so'mgacha bo'lishi kerak."],
      ["18,5", "Narx butun so'mda bo'lishi kerak, masalan 4000."],
    ] as const) {
      type(screen.getByLabelText("Narxi, so'm"), typed);
      fireEvent.click(screen.getByRole("button", { name: "Ro'yxatga qo'shish" }));
      expect(screen.getByRole("alert").textContent).toBe(message);
    }
    expect(screen.queryByRole("list", { name: "Tanlangan tovarlar" })).toBeNull();
  });

  it("goes back to the amount-only form when the last line is removed", async () => {
    const server = shop(() => recorded(45000));
    await openGoods(server);
    pick("Non");
    pick("Sut");
    fireEvent.click(screen.getByRole("button", { name: "Non: olib tashlash" }));
    expect(lineRows()).toHaveLength(1);
    expect(sum()).toBe("Jami: 12 000 so'm");
    fireEvent.click(screen.getByRole("button", { name: "Sut: olib tashlash" }));

    type(screen.getByLabelText("Summa, so'm"), "45000");
    fireEvent.click(submit());
    await screen.findByRole("status");
    expect(writes(server)[0]?.body).toEqual({ kind: "credit", amount: 45000 });
  });

  it.each([
    ["1.2345", "Miqdorda verguldan keyin ko'pi bilan 3 ta raqam bo'ladi."],
    ["0", "Miqdor noldan katta bo'lishi kerak."],
    ["ikki", "Miqdorni raqam bilan yozing, masalan 2 yoki 1,5."],
    ["-1", "Miqdorni raqam bilan yozing, masalan 2 yoki 1,5."],
    ["1000000", "Miqdor juda katta."],
  ])("refuses the quantity %j without calling the server", async (typed, message) => {
    const server = shop(() => recorded(4000));
    await openGoods(server);
    pick("Non");
    type(qty("Non"), typed);
    expect(screen.getByRole("alert").textContent).toBe(message);
    expect(qty("Non").getAttribute("aria-invalid")).toBe("true");
    fireEvent.click(submit());
    expect(writes(server)).toHaveLength(0);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("marks an empty quantity or price only once saving was tried, and saves nothing", async () => {
    const server = shop(() => recorded(4000));
    await openGoods(server);
    pick("Non");
    type(qty("Non"), "");
    type(price("Non"), "");
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(submit());
    expect(screen.getAllByRole("alert").map((alert) => alert.textContent)).toEqual(["Miqdorni kiriting.", "Narxni kiriting."]);
    expect(writes(server)).toHaveLength(0);
  });

  it("refuses a line whose total rounds to nothing and a sum outside 100 to 100 000 000", async () => {
    const server = shop(() => recorded(4000));
    await openGoods(server);
    pick("Tuxum"); // 1 500 a piece
    type(price("Tuxum"), "100");
    type(qty("Tuxum"), "0.001"); // 0.1 so'm
    expect(screen.getByRole("alert").textContent).toBe("Bu qatorning summasi 1 so'mdan kam chiqyapti.");
    fireEvent.click(submit());
    expect(writes(server)).toHaveLength(0);

    type(qty("Tuxum"), "0.99"); // 99 so'm
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(submit());
    expect(screen.getByRole("alert").textContent).toBe("Jami summa kamida 100 so'm bo'lishi kerak.");
    expect(writes(server)).toHaveLength(0);

    type(price("Tuxum"), "100000000");
    type(qty("Tuxum"), "1.001");
    fireEvent.click(submit());
    expect(screen.getByRole("alert").textContent).toBe("Jami summa ko'pi bilan 100 000 000 so'm bo'lishi mumkin.");
    expect(writes(server)).toHaveLength(0);

    type(qty("Tuxum"), "1");
    fireEvent.click(submit());
    await screen.findByRole("status");
    expect(writes(server)).toHaveLength(1);
  });

  it("searches the catalog as the seller types, and Enter in the search does not save the entry", async () => {
    const server = shop(() => recorded(12000));
    await openGoods(server);
    const search = screen.getByLabelText("Katalogdan tovar qidirish");
    type(search, "sut");
    await waitFor(() => expect(server.sent.some((sent) => sent.query["q"] === "sut")).toBe(true));
    await waitFor(() => expect(within(screen.getByRole("list", { name: "Katalogdagi tovarlar" })).getAllByRole("listitem")).toHaveLength(1));

    type(search, "yo'q narsa");
    const notPrevented = fireEvent.keyDown(search, { key: "Enter" });
    expect(notPrevented).toBe(false);
    expect(await screen.findByText("Katalogda topilmadi. Pastda qo'lda yozing.")).toBeTruthy();
    expect(writes(server)).toHaveLength(0);
  });

  it("shows the server's refusal and keeps the lines", async () => {
    const server = shop(() => refusal(409, "LINES_SUM_MISMATCH", "Tovarlar jami yozuv summasiga teng emas."));
    await openGoods(server);
    pick("Non");
    fireEvent.click(submit());
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Tovarlar jami yozuv summasiga teng emas.");
    expect(qty("Non").value).toBe("1");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows an error with a retry when the catalog cannot be read", async () => {
    let failed = false;
    const server = fakeServer((sent) => {
      if (sent.path.endsWith("/catalog") && !failed) {
        failed = true;
        return "offline";
      }
      return sent.path.endsWith("/catalog") ? ok({ items: FIVE_ITEMS, next_cursor: null }) : ok(detailBody());
    });
    await openForm(server);
    fireEvent.click(screen.getByRole("button", { name: "Tovarlar bilan yozish" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi.");
    fireEvent.click(screen.getByRole("button", { name: "Qayta urinish" }));
    expect(await screen.findByRole("list", { name: "Katalogdagi tovarlar" })).toBeTruthy();
  });

  it("does not offer goods on a payment", async () => {
    const server = shop(() => recorded(20000, [], "2026-11-05", "payment"));
    await openForm(server, "payment");
    expect(screen.queryByRole("button", { name: "Tovarlar bilan yozish" })).toBeNull();
    expect(server.sent.filter((sent) => sent.path.endsWith("/catalog"))).toHaveLength(0);
  });

  it("sends one request for a double tap and the same key and lines on a retry", async () => {
    const answer = deferred<{ status: number; body: unknown }>();
    const server = shop((_sent, attempt) => (attempt === 0 ? "offline" : attempt === 1 ? answer.promise : recorded(4000)));
    await openGoods(server);
    pick("Non");

    fireEvent.click(submit());
    await screen.findByText(/Serverga ulanib bo'lmadi/);
    const button = submit();
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.submit(button.closest("form") as HTMLFormElement);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Saqlanmoqda…" }).disabled).toBe(true);
    // Nothing can be changed under a request that is in flight.
    expect(qty("Non").disabled).toBe(true);
    expect(writes(server)).toHaveLength(2);

    answer.resolve(recorded(4000));
    await screen.findByRole("status");
    const sent = writes(server);
    expect(sent).toHaveLength(2);
    expect(sent[1]?.headers["Idempotency-Key"]).toBe(sent[0]?.headers["Idempotency-Key"]);
    expect(sent[1]?.body).toEqual(sent[0]?.body);
  });

  it("uses a new key once a line was changed, because that is another sale", async () => {
    const server = shop((_sent, attempt) => (attempt === 0 ? "offline" : recorded(8000)));
    await openGoods(server);
    pick("Non");
    fireEvent.click(submit());
    await screen.findByText(/Serverga ulanib bo'lmadi/);
    type(qty("Non"), "2");
    fireEvent.click(submit());
    await screen.findByRole("status");
    const keys = writes(server).map((write) => write.headers["Idempotency-Key"]);
    expect(keys[1]).not.toBe(keys[0]);
  });
});

describe("goods lines as pure rules", () => {
  const item: CatalogItem = { id: NON, name: "Non", unit: "dona", price: 4000, learned: false, status: "active", mergedInto: null };
  const draft = (overrides: Partial<DraftLine>): DraftLine => ({
    key: 1,
    catalogItemId: NON,
    name: "Non",
    unit: "dona",
    qtyText: "1",
    priceText: "4000",
    ...overrides,
  });

  it("reads a catalog line and a typed line into what the API takes", () => {
    expect(readDraft(draft({ qtyText: "2,5" }))).toEqual({
      ok: true,
      line: { catalogItemId: NON, qty: 2500, unitPrice: 4000 },
      total: 10000,
    });
    expect(readDraft(draft({ catalogItemId: null, name: "Choy", unit: "" }))).toEqual({
      ok: true,
      line: { name: "Choy", unit: null, qty: 1000, unitPrice: 4000 },
      total: 4000,
    });
  });

  it("names what is wrong with a line", () => {
    expect(readDraft(draft({ qtyText: "", priceText: "x" }))).toEqual({ ok: false, qty: "empty", price: "invalid", tooSmall: false });
    expect(readDraft(draft({ qtyText: "0.001", priceText: "100" }))).toEqual({ ok: false, qty: null, price: null, tooSmall: true });
  });

  it("sums only whole so'm and gives no lines to send while one is unreadable", () => {
    const good = readDrafts([draft({ key: 1, qtyText: "0.125", priceText: "4" }), draft({ key: 2, qtyText: "0.375", priceText: "4" })]);
    expect(good.sum).toBe(3); // 1 + 2: each line is rounded, then the lines are added
    expect(good.lines).toHaveLength(2);
    const bad = readDrafts([draft({ key: 1 }), draft({ key: 2, qtyText: "" })]);
    expect(bad.lines).toBeNull();
    expect(bad.sum).toBe(4000);
  });

  it("does not touch an unreadable quantity when the good is picked again", () => {
    const drafts = [draft({ qtyText: "abc" })];
    expect(pickItem(drafts, item)).toEqual(drafts);
    expect(pickItem([draft({ qtyText: "1,5" })], item)[0]?.qtyText).toBe("2,5");
  });

  it("stops offering goods at fifty lines", async () => {
    const server = shop(() => recorded(4000));
    const fifty = Array.from({ length: 50 }, (_unused, index) => draft({ key: index + 1, catalogItemId: null, name: `Tovar ${index + 1}` }));
    renderScreen(<GoodsEditor drafts={fifty} onChange={() => undefined} showAllProblems={false} disabled={false} />, { fetch: server.fetch });
    expect(screen.getByText("Bitta yozuvda ko'pi bilan 50 ta tovar bo'ladi.")).toBeTruthy();
    expect(screen.queryByLabelText("Katalogdan tovar qidirish")).toBeNull();
    expect(screen.queryByRole("button", { name: "Ro'yxatga qo'shish" })).toBeNull();
    expect(server.sent).toHaveLength(0);

    cleanup();
    renderScreen(<GoodsEditor drafts={fifty.slice(0, 49)} onChange={() => undefined} showAllProblems={false} disabled={false} />, { fetch: server.fetch });
    expect(await screen.findByLabelText("Katalogdan tovar qidirish")).toBeTruthy();
  });
});

describe("promised date right after saving (REQ-008)", () => {
  // The clock of these tests is Tuesday 6 October 2026 in Tashkent.
  const choicePath = `${SHOP_BASE}/entries/${ENTRY_ID}/promise-choice`;
  const chosen = (date: string) => ok({ entry: { id: ENTRY_ID, amount: 45000, promised_date: date }, customer: customerBody({ balance: 165000 }) });
  const offer = () => screen.queryByRole("group", { name: "Qachon to'laydi?" });
  const choices = () => within(offer() as HTMLElement).getAllByRole<HTMLButtonElement>("button");

  async function save(server: ReturnType<typeof fakeServer>, before: () => void = () => undefined) {
    await openForm(server);
    type(screen.getByLabelText("Summa, so'm"), "45000");
    before();
    fireEvent.click(submit());
    return screen.findByRole("status");
  }

  it("offers the four quick choices with their dates after a sale saved with the usual term", async () => {
    const server = shop(() => recorded(45000));
    const done = await save(server);
    expect(done.textContent).toContain("To'lash va'dasi: 2026-yil 5-noyabr");
    expect(choices().map((button) => button.textContent)).toEqual([
      "Ertaga2026-yil 7-oktabr",
      "Hafta oxirigacha2026-yil 11-oktabr",
      "Ikki haftada2026-yil 20-oktabr",
      "Bir oyda2026-yil 6-noyabr",
    ]);
    expect(server.writes()).toHaveLength(1);
  });

  it.each([
    [/^Ertaga/, "2026-10-07", "2026-yil 7-oktabr"],
    [/^Hafta oxirigacha/, "2026-10-11", "2026-yil 11-oktabr"],
    [/^Ikki haftada/, "2026-10-20", "2026-yil 20-oktabr"],
    [/^Bir oyda/, "2026-11-06", "2026-yil 6-noyabr"],
  ])("the choice %s sends %s once and then the choices are gone", async (label, date, shown) => {
    const server = shop((sent) => (sent.path === choicePath ? chosen(date) : recorded(45000)));
    const done = await save(server);
    fireEvent.click(within(offer() as HTMLElement).getByRole("button", { name: label }));
    await waitFor(() => expect(offer()).toBeNull());

    const write = server.writes()[1];
    expect(write).toMatchObject({ method: "POST", path: choicePath });
    expect(write?.body).toEqual({ promised_date: date });
    expect(write?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(write?.headers["Idempotency-Key"]).not.toBe(server.writes()[0]?.headers["Idempotency-Key"]);
    expect(done.textContent).toContain(`To'lash va'dasi: ${shown}`);
    expect(done.textContent).not.toContain("5-noyabr");
    expect(server.writes()).toHaveLength(2);
  });

  it("takes the choices away after a 409 and shows the server's message", async () => {
    const message = "Muddat allaqachon belgilangan. Endi uni menejer yoki do'kon egasi o'zgartiradi.";
    const server = shop((sent) => (sent.path === choicePath ? refusal(409, "PROMISE_ALREADY_SET", message) : recorded(45000)));
    const done = await save(server);
    fireEvent.click(choices()[0] as HTMLElement);
    expect((await screen.findByRole("alert")).textContent).toBe(message);
    expect(offer()).toBeNull();
    // The date the sale was saved with still stands.
    expect(done.textContent).toContain("To'lash va'dasi: 2026-yil 5-noyabr");
  });

  it("sends one request for a double tap, and the same key again after a lost connection", async () => {
    const answer = deferred<{ status: number; body: unknown }>();
    let attempt = 0;
    const server = shop((sent) => {
      if (sent.path !== choicePath) {
        return recorded(45000);
      }
      attempt += 1;
      return attempt === 1 ? "offline" : answer.promise;
    });
    await save(server);
    fireEvent.click(choices()[0] as HTMLElement);
    expect((await screen.findByRole("alert")).textContent).toContain("Serverga ulanib bo'lmadi.");
    // The choice may or may not have arrived: it is still offered, and trying again is safe.
    expect(choices()).toHaveLength(4);

    fireEvent.click(choices()[0] as HTMLElement);
    fireEvent.click(choices()[0] as HTMLElement);
    fireEvent.click(choices()[1] as HTMLElement);
    expect(choices().every((button) => button.disabled)).toBe(true);
    answer.resolve(chosen("2026-10-07"));
    await waitFor(() => expect(offer()).toBeNull());

    const sent = server.writes().filter((write) => write.path === choicePath);
    expect(sent).toHaveLength(2);
    expect(sent[1]?.headers["Idempotency-Key"]).toBe(sent[0]?.headers["Idempotency-Key"]);
    expect(sent.map((write) => write.body)).toEqual([{ promised_date: "2026-10-07" }, { promised_date: "2026-10-07" }]);
  });

  it("is not offered when a date was chosen on the form", async () => {
    const server = shop(() => recorded(45000, [], "2026-10-07"));
    await save(server, () => fireEvent.click(screen.getByRole("radio", { name: /^Ertaga/ })));
    expect(offer()).toBeNull();
    expect(server.writes()[0]?.body).toEqual({ kind: "credit", amount: 45000, promised_date: "2026-10-07" });
  });

  it("is not offered after a payment", async () => {
    const server = shop(() => ok({ entry: { id: ENTRY_ID, kind: "payment", amount: 20000, promised_date: null }, customer: customerBody({ balance: 100000 }) }, 201));
    await openForm(server, "payment");
    type(screen.getByLabelText("Summa, so'm"), "20000");
    fireEvent.click(screen.getByRole("button", { name: "To'lovni yozish" }));
    await screen.findByRole("status");
    expect(offer()).toBeNull();
  });

  it("is offered after an itemized sale saved with the usual term too", async () => {
    const server = shop((sent) => (sent.path === choicePath ? chosen("2026-10-20") : recorded(4000, [lineBody({ qty: "1", line_total: 4000 })])));
    await openGoods(server);
    pick("Non");
    fireEvent.click(submit());
    await screen.findByRole("status");
    fireEvent.click(within(offer() as HTMLElement).getByRole("button", { name: /^Ikki haftada/ }));
    await waitFor(() => expect(offer()).toBeNull());
    expect(server.writes()[1]?.body).toEqual({ promised_date: "2026-10-20" });
  });
});
