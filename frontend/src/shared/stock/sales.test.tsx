// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { fakeServer, ok, refusal, type Reply, type Sent, SHOP_ID } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import { type ApiError, BAD_RESPONSE, createApi } from "../api";
import { addToSale, SaleScreen, stepOf, stepQty } from "./SaleScreen";
import { MAX_SALES_DAYS, readPeriod, SalesScreen } from "./SalesScreen";
import { saleBody as requestBody, stockOf, type StockItem, type StockSettings } from "./stockApi";
import { StockItemScreen } from "./StockItemScreen";
import { StockReportScreen } from "./StockReportScreen";
import { StockScreen } from "./StockScreen";
import {
  costedSaleBody,
  EAN,
  ITEM_ID,
  MEMBER,
  OTHER_ITEM,
  OTHER_MEMBER,
  OTHER_SALE,
  SALE_ID,
  saleBody,
  saleLineBody,
  saleRowBody,
  salesListBody,
  STOCK,
  stockItemBody,
  stockReportBody,
  stockSettingsBody,
  UNKNOWN_EAN,
} from "./testing";

/**
 * A sale for cash, without a customer: the counter's form, what it says after the sale, the day's
 * sales, one of them taken back, and the three places the stock names such a sale (an item's
 * movements, the report, the stock's own screen). Each rule has the case that would break it.
 */

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

const FIND = "Tovar: shtrix-kodni skanerlang yoki nom yozing";
const NOT_FOUND = refusal(404, "NOT_FOUND", "Topilmadi.");
/** A second code with a right check digit: the tea's, sold by the piece. */
const TEA_EAN = UNKNOWN_EAN;
const SELLER = { role: "seller" as const };
const MANAGER = { role: "manager" as const };

/** An amount as the screens write it: the groups of digits are joined by no-break spaces. */
const som = (text: string) => text.replace(/(\d) (?=\d)/g, "$1 ");

const sugar = () => stockItemBody();
const tea = () => stockItemBody({ id: OTHER_ITEM, name: "Choy", unit: "dona", price: 8000, on_hand: "12", barcodes: [TEA_EAN] });
const recorded = (overrides: Record<string, unknown> = {}) => ({ ...saleBody(), warnings: [], ...overrides });

type Options = {
  cashBook?: boolean;
  sell?: (sent: Sent) => Reply;
  cancel?: (sent: Sent) => Reply;
  list?: (sent: Sent) => unknown;
  /** The list is refused instead of answered. */
  listFails?: () => boolean;
  sale?: () => Reply;
};

function backend(options: Options = {}) {
  return fakeServer((sent) => {
    if (sent.method === "POST" && sent.path === `${STOCK}/sales`) {
      return options.sell?.(sent) ?? ok(recorded(), 201);
    }
    if (sent.method === "POST" && sent.path === `${STOCK}/sales/${SALE_ID}/cancel`) {
      return (
        options.cancel?.(sent) ??
        ok(saleBody({ status: "cancelled", cancelled_at: "2026-10-06T06:40:00+00:00", cancel_reason: (sent.body as { reason: string }).reason }))
      );
    }
    if (sent.method !== "GET") {
      return NOT_FOUND;
    }
    switch (sent.path) {
      case `${STOCK}/settings`:
        return ok(stockSettingsBody({ cash_book: options.cashBook ?? false }));
      case `${STOCK}/items`:
        return ok({ items: [sugar(), tea()], next_cursor: null });
      case `${STOCK}/lookup`:
        return sent.query["code"] === EAN ? ok(sugar()) : sent.query["code"] === TEA_EAN ? ok(tea()) : NOT_FOUND;
      case `${STOCK}/sales`:
        if (options.listFails?.()) {
          return refusal(500, "INTERNAL", "Serverda xatolik.");
        }
        return ok(options.list?.(sent) ?? salesListBody([saleRowBody()]));
      case `${STOCK}/sales/${SALE_ID}`:
        return options.sale?.() ?? ok(saleBody());
      default:
        return NOT_FOUND;
    }
  });
}

/** Types into the finder and presses Enter, as a person or a scanner that acts as a keyboard does. */
function find(text: string) {
  const input = screen.getByLabelText(FIND);
  fireEvent.change(input, { target: { value: text } });
  fireEvent.submit(input.closest("form") as HTMLFormElement);
}

async function counter(server = backend(), who: { role: "seller" | "manager" | "owner"; permissions?: string[] } = SELLER) {
  renderScreen(<SaleScreen host={{}} />, { fetch: server.fetch, ...who });
  await screen.findByLabelText(FIND);
  return server;
}

/** Scans a code and waits for its line. */
async function scan(code: string, name: string) {
  find(code);
  return (await screen.findByLabelText(new RegExp(`^«${name}»: miqdor`))) as HTMLInputElement;
}

const lines = () => within(screen.getByRole("list", { name: "Savdodagi tovarlar" })).getAllByRole("listitem");
const sales = (server: ReturnType<typeof fakeServer>) => server.sent.filter((sent) => sent.method === "POST" && sent.path === `${STOCK}/sales`);
/** Everything a sale in full shows: its facts and its lines. */
const shownOfSale = () => `${document.querySelector("dl.facts")?.textContent} ${screen.getByRole("list", { name: "Savdodagi tovarlar" }).textContent}`;
const fact = (name: string) => screen.getByText(name, { selector: "dt" }).nextElementSibling?.textContent;

describe("a sale at the counter", () => {
  it("finds an item by name, takes one of it at its own price, and is sold with one press", async () => {
    const server = await counter();
    // Nothing to sell yet: no button that would send an empty sale.
    expect(screen.getByText("Tovarni skanerlang yoki nomi bo'yicha toping: u savdoga qo'shiladi.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sotish" })).toBeNull();

    find("shak");
    fireEvent.click(await within(await screen.findByRole("list", { name: "Topilgan tovarlar" })).findByRole("button", { name: /Shakar/ }));
    // Every item of the catalog is offered, the ones that are not counted too.
    expect(server.sent.find((sent) => sent.path === `${STOCK}/items`)?.query).toEqual({ q: "shak", filter: "all", limit: "20" });

    expect((screen.getByLabelText("«Shakar»: miqdor (kg)") as HTMLInputElement).value).toBe("1");
    expect((screen.getByLabelText("«Shakar»: bir birlik narxi, so'm") as HTMLInputElement).value).toBe("15000");
    expect(lines()[0]?.textContent).toContain("Omborda: 7,5 kg");
    expect(screen.getByText("Jami").parentElement?.textContent).toBe(som("Jami 15 000 so'm"));
    expect(server.writes()).toEqual([]);

    // The one press after the item was found.
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    await waitFor(() => expect(sales(server)).toHaveLength(1));
    expect(sales(server)[0]?.body).toEqual({ lines: [{ item_id: ITEM_ID, qty: "1", price: 15000 }], method: "cash" });
    expect(sales(server)[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);

    const done = await screen.findByRole("status");
    expect(done.textContent).toBe(som("Naqd savdo № 3 yozildi.Jami 37 500 so'm"));
    expect(screen.getByRole("heading", { name: /Naqd savdo № 3/ }).textContent).toBe("Naqd savdo № 3 Yozilgan");
    expect(fact("Kim sotdi")).toBe("Men");
    expect(fact("To'lov usuli")).toBe("Naqd");
    expect(within(screen.getByRole("list", { name: "Savdodagi tovarlar" })).getAllByRole("listitem")[0]?.textContent).toBe(
      som("ShakarMiqdor2,5 kgNarxi15 000 so'mSumma37 500 so'm"),
    );
    // The form is gone until the next sale is begun: nothing can be sent twice from it.
    expect(screen.queryByRole("button", { name: "Sotish" })).toBeNull();
    expect(screen.getByRole("link", { name: "Naqd savdolar" }).getAttribute("href")).toBe("#/stock/sales");
  });

  it("adds to the quantity when the same item is scanned again: one line for an item", async () => {
    const server = await counter();
    const pieces = await scan(TEA_EAN, "Choy");
    expect(pieces.value).toBe("1");
    find(TEA_EAN);
    await waitFor(() => expect(pieces.value).toBe("2"));
    find(TEA_EAN);
    await waitFor(() => expect(pieces.value).toBe("3"));
    expect(lines()).toHaveLength(1);
    // A weighed good goes up by a tenth of its unit, and is still one line.
    const weight = await scan(EAN, "Shakar");
    find(EAN);
    await waitFor(() => expect(weight.value).toBe("1,1"));
    expect(lines().map((line) => line.querySelector(".row__name")?.textContent)).toEqual(["Choy", "Shakar"]);
    // 3 × 8 000 + 1.1 × 15 000.
    expect(screen.getByText("Jami").parentElement?.textContent).toBe(som("Jami 40 500 so'm"));

    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    await waitFor(() => expect(sales(server)).toHaveLength(1));
    // Never the same item twice: the server refuses that.
    expect(sales(server)[0]?.body).toEqual({
      lines: [
        { item_id: OTHER_ITEM, qty: "3", price: 8000 },
        { item_id: ITEM_ID, qty: "1.1", price: 15000 },
      ],
      method: "cash",
    });
  });

  it("steps a quantity up and down, by one piece or by a tenth of a weighed unit, and never to nothing", async () => {
    await counter();
    const pieces = await scan(TEA_EAN, "Choy");
    const less = screen.getByRole("button", { name: "«Choy»: kamaytirish" }) as HTMLButtonElement;
    const more = screen.getByRole("button", { name: "«Choy»: ko'paytirish" });
    // One piece cannot become none: the line is removed with its own button instead.
    expect(less.disabled).toBe(true);
    fireEvent.click(more);
    fireEvent.click(more);
    expect(pieces.value).toBe("3");
    expect(pieces.inputMode).toBe("numeric");
    fireEvent.click(less);
    expect(pieces.value).toBe("2");
    expect(lines()[0]?.textContent).toContain(som("16 000 so'm"));

    const weight = await scan(EAN, "Shakar");
    expect(weight.inputMode).toBe("decimal");
    fireEvent.click(screen.getByRole("button", { name: "«Shakar»: kamaytirish" }));
    expect(weight.value).toBe("0,9");
    fireEvent.change(weight, { target: { value: "0,1" } });
    expect((screen.getByRole("button", { name: "«Shakar»: kamaytirish" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "«Choy» qatorini olib tashlash" }));
    expect(lines()).toHaveLength(1);
  });

  it("sells at the price typed for this sale, a typed quantity with a comma, and by the way that was chosen", async () => {
    const server = await counter();
    const weight = await scan(EAN, "Shakar");
    fireEvent.change(weight, { target: { value: "2,5" } });
    fireEvent.change(screen.getByLabelText("«Shakar»: bir birlik narxi, so'm"), { target: { value: "14000" } });
    expect(screen.getByText("Jami").parentElement?.textContent).toBe(som("Jami 35 000 so'm"));
    const ways = within(screen.getByRole("group", { name: "To'lov usuli" })).getAllByRole("button");
    expect(ways.map((way) => [way.textContent, way.getAttribute("aria-pressed")])).toEqual([
      ["Naqd", "true"],
      ["Karta", "false"],
      ["O'tkazma", "false"],
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Karta" }));
    fireEvent.change(screen.getByLabelText("Izoh"), { target: { value: "  do'kon   oldida " } });
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    await waitFor(() => expect(sales(server)).toHaveLength(1));
    expect(sales(server)[0]?.body).toEqual({ lines: [{ item_id: ITEM_ID, qty: "2.5", price: 14000 }], method: "card", note: "do'kon oldida" });
  });

  it("sends nothing while a quantity or a price cannot be read, and says which", async () => {
    const server = await counter();
    // A good line beside the one that cannot be read: the sale is not sent without the bad line either.
    await scan(TEA_EAN, "Choy");
    const weight = await scan(EAN, "Shakar");
    const price = screen.getByLabelText("«Shakar»: bir birlik narxi, so'm");
    fireEvent.change(weight, { target: { value: "abc" } });
    expect(screen.getByText("Miqdor noldan katta bo'lsin, masalan 12 yoki 1,5. Ko'pi bilan uchta kasr xona.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    // An emptied price is said only once the sale was tried.
    fireEvent.change(weight, { target: { value: "1" } });
    fireEvent.change(price, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    expect(screen.getByText("Narxni butun so'mda yozing, masalan 15000.")).toBeTruthy();
    // A line that comes to less than one so'm is refused here as the server would refuse it.
    fireEvent.change(weight, { target: { value: "0,001" } });
    fireEvent.change(price, { target: { value: "100" } });
    expect(screen.getByText("Bu qator 1 so'mdan kam chiqadi. Miqdor yoki narxni tekshiring.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    expect(server.writes()).toEqual([]);

    fireEvent.change(weight, { target: { value: "0,5" } });
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    await waitFor(() => expect(sales(server)).toHaveLength(1));
    expect(sales(server)[0]?.body).toEqual({
      lines: [
        { item_id: OTHER_ITEM, qty: "1", price: 8000 },
        { item_id: ITEM_ID, qty: "0.5", price: 100 },
      ],
      method: "cash",
    });
  });

  it("says that the money goes into the cash book only in a shop that keeps one", async () => {
    await counter(backend({ cashBook: true }));
    await scan(EAN, "Shakar");
    expect(screen.getByText("Savdo kassaga kirim bo'lib yoziladi.")).toBeTruthy();
    cleanup();
    await counter(backend({ cashBook: false }));
    await scan(EAN, "Shakar");
    expect(screen.queryByText("Savdo kassaga kirim bo'lib yoziladi.")).toBeNull();
  });

  it("shows a refusal for want of stock with the server's words, the item and what is on hand, and keeps the sale to be changed", async () => {
    let refuse = true;
    const server = await counter(
      backend({
        sell: () =>
          refuse
            ? refusal(409, "STOCK_INSUFFICIENT", "Omborda yetarli tovar yo'q.", { item: ITEM_ID, name: "Shakar", on_hand: "1.5", wanted: "2.5" })
            : ok(recorded(), 201),
      }),
    );
    const weight = await scan(EAN, "Shakar");
    fireEvent.change(weight, { target: { value: "2,5" } });
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Omborda yetarli tovar yo'q.«Shakar»: omborda 1,5, sotuvga 2,5 kerak.");
    // Nothing was saved: no result, and the line is still there.
    expect(screen.queryByText(/yozildi\./)).toBeNull();
    expect(weight.value).toBe("2,5");

    // The same sale sent again is the same request to the server; a changed one is a new request.
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    await waitFor(() => expect(sales(server)).toHaveLength(2));
    expect(sales(server)[1]?.headers["Idempotency-Key"]).toBe(sales(server)[0]?.headers["Idempotency-Key"]);
    await screen.findByRole("alert");
    fireEvent.change(weight, { target: { value: "1,5" } });
    expect(screen.queryByRole("alert")).toBeNull();
    refuse = false;
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    await screen.findByText("Naqd savdo № 3 yozildi.");
    expect(sales(server)[2]?.headers["Idempotency-Key"]).not.toBe(sales(server)[0]?.headers["Idempotency-Key"]);
  });

  it("names the line a check of the server failed on, and the server's own words for any other refusal", async () => {
    const answers: Reply[] = [
      refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri.", { "lines.1.price": "a whole amount between 1 and 100000000 UZS" }),
      refusal(403, "SHOP_SUSPENDED", "Do'kon vaqtincha to'xtatilgan."),
    ];
    const server = await counter(backend({ sell: () => answers.shift() ?? NOT_FOUND }));
    await scan(TEA_EAN, "Choy");
    await scan(EAN, "Shakar");
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Ma'lumotlar noto'g'ri.2-qatorni tekshiring.");
    fireEvent.click(screen.getByRole("button", { name: "«Choy»: ko'paytirish" }));
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    await waitFor(() => expect(sales(server)).toHaveLength(2));
    expect((await screen.findByRole("alert")).textContent).toBe("Do'kon vaqtincha to'xtatilgan.");
  });

  it("tells the seller what the sale did to the stock, and that it was below cost only when the server said so", async () => {
    await counter(
      backend({
        sell: () =>
          ok(
            recorded({
              warnings: [
                { kind: "negative", item: ITEM_ID, name: "Shakar", on_hand: "-2" },
                { kind: "below_cost", item: ITEM_ID, name: "Shakar" },
                // A kind of a later server: left out, the sale is saved either way.
                { kind: "expires_soon", item: ITEM_ID, name: "Shakar" },
              ],
            }),
            201,
          ),
      }),
    );
    await scan(EAN, "Shakar");
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    const done = await screen.findByRole("status");
    expect([...done.querySelectorAll(".row__warning")].map((warning) => warning.textContent)).toEqual([
      "«Shakar» omborda minusga tushdi: qoldiq -2. Kirimni yozib qo'ying.",
      "«Shakar» tannarxidan arzon sotildi.",
    ]);
    cleanup();

    // A seller is sent no such warning, and none is made up here: the price is far below any cost.
    await counter(backend({ sell: () => ok(recorded({ warnings: [{ kind: "negative", item: ITEM_ID, name: "Shakar", on_hand: "-2" }] }), 201) }));
    await scan(EAN, "Shakar");
    fireEvent.change(screen.getByLabelText("«Shakar»: bir birlik narxi, so'm"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    await screen.findByText("Naqd savdo № 3 yozildi.");
    expect(screen.queryByText(/tannarx/i)).toBeNull();
    expect(document.querySelectorAll(".row__warning")).toHaveLength(1);
  });

  it("shows nothing of cost where the server sent none, whatever the role, and what it sent where it did", async () => {
    // A manager by role from whom the owner took the sight of cost: the answer has no cost in it.
    await counter(backend(), MANAGER);
    await scan(EAN, "Shakar");
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    await screen.findByText("Naqd savdo № 3 yozildi.");
    for (const word of ["Tannarx", "Foyda"]) {
      expect(screen.queryByText(word)).toBeNull();
    }
    expect(shownOfSale()).not.toMatch(/tannarx|foyda/i);
    cleanup();

    const sold = costedSaleBody({
      total: 45500,
      in_cash_book: true,
      lines: [
        saleLineBody({ cost: { currency: "UZS", total: 30000, margin: 7500 } }),
        // Not counted in the stock: sold without a movement, and with no cost to speak of.
        saleLineBody({ line_no: 2, item: { id: OTHER_ITEM, name: "Choy", unit: "dona" }, qty: "1", price: 8000, line_total: 8000, counted: false, cost: { currency: null, total: null, margin: null } }),
      ],
    });
    await counter(backend({ sell: () => ok({ ...sold, warnings: [] }, 201) }), MANAGER);
    await scan(EAN, "Shakar");
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    await screen.findByText("Naqd savdo № 3 yozildi.");
    expect(fact("Tannarx")).toBe(som("30 000 so'm"));
    expect(fact("Foyda")).toBe(som("7 500 so'm"));
    expect(within(screen.getByRole("list", { name: "Savdodagi tovarlar" })).getAllByRole("listitem").map((row) => row.textContent)).toEqual([
      som("ShakarMiqdor2,5 kgNarxi15 000 so'mSumma37 500 so'mTannarx30 000 so'mFoyda7 500 so'm"),
      som("Choy Omborda hisoblanmaydiMiqdor1 donaNarxi8 000 so'mSumma8 000 so'mTannarx—Foyda—"),
    ]);
    expect(screen.getByText("Kassaga kirim bo'lib yozildi.")).toBeTruthy();
  });

  it("says when the profit is of some lines only, and shows no figure when no cost is known", async () => {
    const partial = costedSaleBody({ cost: { total: 30000, margin: 7500, complete: false } });
    await counter(backend({ sell: () => ok({ ...partial, warnings: [] }, 201) }), MANAGER);
    await scan(EAN, "Shakar");
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    expect(await screen.findByText("Foyda tannarxi so'mda ma'lum bo'lgan tovarlar bo'yicha hisoblangan.")).toBeTruthy();
    cleanup();

    const unknown = costedSaleBody({
      lines: [saleLineBody({ cost: { currency: "USD", total: 250, margin: null } })],
      cost: { total: 0, margin: null, complete: false },
    });
    await counter(backend({ sell: () => ok({ ...unknown, warnings: [] }, 201) }), MANAGER);
    await scan(EAN, "Shakar");
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    await screen.findByText("Naqd savdo № 3 yozildi.");
    // A cost kept in dollars is shown as it is, and is never taken from so'm: no margin, no zero.
    expect(fact("Tannarx")).toBe("—");
    expect(fact("Foyda")).toBe("—");
    expect(within(screen.getByRole("list", { name: "Savdodagi tovarlar" })).getAllByRole("listitem")[0]?.textContent).toContain("Tannarx2.50 $Foyda—");
  });

  it("begins the next sale with 'Yangi savdo', or with the next good that is scanned", async () => {
    const server = await counter();
    await scan(EAN, "Shakar");
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    const again = await screen.findByRole("button", { name: "Yangi savdo" });
    // The focus is on it: Enter begins the next sale.
    expect(document.activeElement).toBe(again);
    fireEvent.click(again);
    expect(screen.queryByText("Naqd savdo № 3 yozildi.")).toBeNull();
    expect(screen.queryByRole("list", { name: "Savdodagi tovarlar" })).toBeNull();
    expect(screen.getByText("Tovarni skanerlang yoki nomi bo'yicha toping: u savdoga qo'shiladi.")).toBeTruthy();

    await scan(TEA_EAN, "Choy");
    fireEvent.click(screen.getByRole("button", { name: "Sotish" }));
    await screen.findByText("Naqd savdo № 3 yozildi.");
    // A scan while the last sale is still shown: the result goes, and the new sale has that one good.
    const next = await scan(EAN, "Shakar");
    expect(next.value).toBe("1");
    expect(screen.queryByText("Naqd savdo № 3 yozildi.")).toBeNull();
    expect(lines()).toHaveLength(1);
    expect(sales(server).map((sent) => sent.headers["Idempotency-Key"]).filter((key, index, all) => all.indexOf(key) === index)).toHaveLength(2);
  });

  it("is not a screen without stock.sell, nor without stock.view, and nothing is asked for such a member", async () => {
    for (const permissions of [["stock.view"], ["stock.sell"], ["stock.view", "stock.receive", "stock.sell.cancel"], []]) {
      const server = backend();
      renderScreen(<SaleScreen host={{}} />, { fetch: server.fetch, role: "owner", permissions });
      expect(await screen.findByText("Bosh sahifaga qaytish")).toBeTruthy();
      expect(server.sent, permissions.join()).toEqual([]);
      cleanup();
      renderScreen(<SalesScreen />, { fetch: server.fetch, role: "owner", permissions });
      expect(await screen.findByText("Bosh sahifaga qaytish")).toBeTruthy();
      cleanup();
      renderScreen(<SalesScreen saleId={SALE_ID} />, { fetch: server.fetch, role: "owner", permissions });
      expect(await screen.findByText("Bosh sahifaga qaytish")).toBeTruthy();
      expect(server.sent, permissions.join()).toEqual([]);
      cleanup();
    }
    // With both it is: by permission, for a seller by role as for anyone.
    await counter(backend(), { role: "seller", permissions: ["stock.view", "stock.sell"] });
    expect(screen.getByLabelText(FIND)).toBeTruthy();
  });
});

describe("the arithmetic of a sale's lines", () => {
  const settings = { units: [{ key: "kg", weighed: true }, { key: "dona", weighed: false }] } as unknown as StockSettings;
  const item = (unit: string) => ({ id: unit, unit, price: 100 }) as StockItem;

  it("steps by a piece, or by a tenth of a weighed unit; a unit the stock does not name is stepped by one", () => {
    expect(stepOf("dona", settings)).toBe(1000);
    expect(stepOf("kg", settings)).toBe(100);
    expect(stepOf("quti", settings)).toBe(1000);
  });

  it("never makes a fraction of binary arithmetic, never nothing, never more than a quantity can be", () => {
    expect(stepQty("0,2", 100)).toBe("0,3");
    expect(stepQty("1.5", -100)).toBe("1,4");
    expect(stepQty("0,1", -100)).toBe("0,1");
    expect(stepQty("1", -1000)).toBe("1");
    expect(stepQty("999999,999", 100)).toBe("999999,999");
    // What cannot be read is left for its author to mend when stepped down, and becomes one step up.
    expect(stepQty("abc", -1000)).toBe("abc");
    expect(stepQty("", 1000)).toBe("1");
  });

  it("adds an item once, and then to its quantity", () => {
    const once = addToSale([], item("dona"), settings);
    expect(once).toEqual([{ item: item("dona"), qtyText: "1", priceText: "100" }]);
    const twice = addToSale(addToSale(once, item("kg"), settings), item("dona"), settings);
    expect(twice.map((line) => [line.item.id, line.qtyText])).toEqual([
      ["dona", "2"],
      ["kg", "1"],
    ]);
    // A price typed for this sale is kept when the item is scanned again.
    const priced = addToSale([{ item: item("kg"), qtyText: "2,5", priceText: "90" }], item("kg"), settings);
    expect(priced).toEqual([{ item: item("kg"), qtyText: "2,6", priceText: "90" }]);
  });
});

const mine = () => saleRowBody();
const others = () =>
  saleRowBody({
    id: OTHER_SALE,
    number: 2,
    created_at: "2026-10-06T05:10:00+00:00",
    created_by: OTHER_MEMBER,
    seller_role: "manager",
    mine: false,
    method: "card",
    total: 16000,
    lines: [saleLineBody({ item: { id: OTHER_ITEM, name: "Choy", unit: "dona" }, qty: "2", price: 8000, line_total: 16000, counted: null })],
  });
const taken = () =>
  saleRowBody({ id: "88888888-8888-4888-8888-8888888888a3", number: 1, status: "cancelled", cancelled_at: "2026-10-06T05:00:00+00:00", cancel_reason: "xato" });
const asked = (server: ReturnType<typeof fakeServer>) => server.sent.filter((sent) => sent.method === "GET" && sent.path === `${STOCK}/sales`).map((sent) => sent.query);
const rows = () => within(screen.getByRole("list", { name: "Naqd savdolar" })).getAllByRole("listitem");
const figures = () => [...document.querySelectorAll(".figure")].map((figure) => figure.textContent);

describe("the day's cash sales at the counter", () => {
  it("asks for no day, which is the server's today, and shows the sales newest first with what they came to", async () => {
    const server = backend({ list: () => salesListBody([mine(), others(), taken()]) });
    renderScreen(<SalesScreen />, { fetch: server.fetch, ...SELLER });
    await screen.findByRole("list", { name: "Naqd savdolar" });
    expect(asked(server)).toEqual([{}]);
    expect(screen.getByRole("heading", { name: "Bugungi savdolar" })).toBeTruthy();
    expect(rows().map((row) => row.textContent)).toEqual([
      som("Naqd savdo № 3Shakar 2,5 kgVaqt2026-yil 6-oktabr, 11:30Jami37 500 so'mTo'lov usuliNaqdKim sotdiMen"),
      som("Naqd savdo № 2Choy 2 donaVaqt2026-yil 6-oktabr, 10:10Jami16 000 so'mTo'lov usuliKartaKim sotdiMenejer"),
      som("Naqd savdo № 1 Bekor qilinganShakar 2,5 kgVaqt2026-yil 6-oktabr, 11:30Jami37 500 so'mTo'lov usuliNaqdKim sotdiMen"),
    ]);
    // The cancelled one is in none of the figures, which are the server's own.
    expect(figures()).toEqual([som("Savdolar soni2"), som("Jami tushum53 500 so'm"), som("Naqd37 500 so'm"), som("Karta16 000 so'm")]);
    expect(within(rows()[0] as HTMLElement).getByRole("link", { name: "Naqd savdo № 3" }).getAttribute("href")).toBe(`#/stock/sales/${SALE_ID}`);
    expect(screen.getByRole("link", { name: "Yangi savdo" }).getAttribute("href")).toBe("#/stock/sale");
    // No filters of the web panel here, and no table: a phone reads rows.
    expect(screen.queryByLabelText("Qaysi kundan")).toBeNull();
    expect(document.querySelector("table")).toBeNull();
  });

  it("narrows to the reader's own sales and back, and says when there are none", async () => {
    const server = backend({ list: (sent) => (sent.query["mine"] === "true" ? salesListBody([]) : salesListBody([mine(), others()])) });
    renderScreen(<SalesScreen />, { fetch: server.fetch, ...SELLER });
    await screen.findByRole("list", { name: "Naqd savdolar" });
    expect(screen.getByRole("button", { name: "Hammasi" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Meniki" }));
    expect(await screen.findByText("Bugun hali naqd savdo yo'q.")).toBeTruthy();
    expect(asked(server).at(-1)).toEqual({ mine: "true" });
    expect(figures()).toEqual(["Savdolar soni0", "Jami tushum0 so'm"]);
    fireEvent.click(screen.getByRole("button", { name: "Hammasi" }));
    await screen.findByRole("list", { name: "Naqd savdolar" });
    expect(asked(server).at(-1)).toEqual({});
  });
});

describe("the cash sales in the web panel", () => {
  const open = async (server: ReturnType<typeof fakeServer>) => {
    renderScreen(<SalesScreen office />, { fetch: server.fetch, ...MANAGER });
    await screen.findByRole("list", { name: "Naqd savdolar" });
  };

  it("asks for today by its date, and for the days, the state and the seller that are chosen", async () => {
    const server = backend({ list: () => salesListBody([mine(), others()]) });
    await open(server);
    // Tuesday 6 October 2026 in Tashkent, said outright: the two fields show what was asked.
    expect(asked(server)).toEqual([{ day_from: "2026-10-06", day_to: "2026-10-06" }]);
    expect((screen.getByLabelText("Qaysi kundan") as HTMLInputElement).value).toBe("2026-10-06");
    expect(screen.queryByRole("button", { name: "Meniki" })).toBeNull();

    fireEvent.change(screen.getByLabelText("Qaysi kundan"), { target: { value: "2026-10-01" } });
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ day_from: "2026-10-01", day_to: "2026-10-06" }));
    fireEvent.change(screen.getByLabelText("Holati"), { target: { value: "cancelled" } });
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ day_from: "2026-10-01", day_to: "2026-10-06", status: "cancelled" }));
    expect([...(screen.getByLabelText("Holati") as HTMLSelectElement).options].map((option) => option.textContent)).toEqual([
      "Barcha holatlar",
      "Yozilgan",
      "Bekor qilingan",
    ]);

    // Who sold: the reader, and the members the rows read so far name, each by role.
    await screen.findByRole("list", { name: "Naqd savdolar" });
    const who = screen.getByLabelText("Kim sotdi") as HTMLSelectElement;
    expect([...who.options].map((option) => option.textContent)).toEqual(["Barcha sotuvchilar", "Men", "Menejer · 333334"]);
    fireEvent.change(who, { target: { value: "mine" } });
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ day_from: "2026-10-01", day_to: "2026-10-06", status: "cancelled", mine: "true" }));
    fireEvent.change(who, { target: { value: OTHER_MEMBER } });
    await waitFor(() =>
      expect(asked(server).at(-1)).toEqual({ day_from: "2026-10-01", day_to: "2026-10-06", status: "cancelled", seller_id: OTHER_MEMBER }),
    );
    fireEvent.change(who, { target: { value: "all" } });
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ day_from: "2026-10-01", day_to: "2026-10-06", status: "cancelled" }));
    // Never the two together, which the server refuses.
    expect(asked(server).filter((query) => "mine" in query && "seller_id" in query)).toEqual([]);
    // The reader's own membership is not among the others: "Men" is how their sales are asked for.
    expect([...who.options].map((option) => option.value)).not.toContain(MEMBER);
  });

  it("narrows to one item, found by name among all the goods", async () => {
    const server = backend();
    await open(server);
    const choice = screen.getByRole("combobox", { name: "Tovar" }) as HTMLInputElement;
    // The goods are asked for when the choice is opened, not with the screen.
    expect(server.sent.filter((sent) => sent.path === `${STOCK}/items`)).toEqual([]);
    fireEvent.click(choice);
    const offered = await screen.findByRole("option", { name: /Choy/ });
    expect(within(screen.getByRole("listbox", { name: "Tovar" })).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Barcha tovarlar",
      "Shakarkg",
      "Choydona",
    ]);
    fireEvent.click(offered);
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ day_from: "2026-10-06", day_to: "2026-10-06", item_id: OTHER_ITEM }));
    expect(choice.value).toBe("Choy");
    fireEvent.click(choice);
    fireEvent.click(await screen.findByRole("option", { name: "Barcha tovarlar" }));
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ day_from: "2026-10-06", day_to: "2026-10-06" }));
  });

  it("asks nothing for days that are no stretch the server takes, and says so", async () => {
    const server = backend();
    await open(server);
    const wrong = "Davrni to'g'ri tanlang: oxirgi kun birinchisidan oldin bo'lmasin, oraliq 366 kundan oshmasin.";
    for (const from of ["2026-10-07", "2025-10-05", ""]) {
      fireEvent.change(screen.getByLabelText("Qaysi kundan"), { target: { value: from } });
      expect(screen.getByText(wrong), from).toBeTruthy();
      expect(screen.getByLabelText("Qaysi kundan").getAttribute("aria-invalid")).toBe("true");
    }
    expect(asked(server)).toHaveLength(1);
    // 366 days and no more.
    fireEvent.change(screen.getByLabelText("Qaysi kundan"), { target: { value: "2025-10-06" } });
    await waitFor(() => expect(asked(server).at(-1)).toEqual({ day_from: "2025-10-06", day_to: "2026-10-06" }));
    expect(screen.queryByText(wrong)).toBeNull();
  });

  it("reads the next page with the server's cursor, under the same filters", async () => {
    const server = backend({
      list: (sent) => (sent.query["cursor"] === "c2" ? salesListBody([others()]) : salesListBody([mine()], { next_cursor: "c2", totals: { count: 2, total: 53500, by_method: [] } })),
    });
    await open(server);
    expect(rows()).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Yana ko'rsatish" }));
    await waitFor(() => expect(rows()).toHaveLength(2));
    expect(asked(server).at(-1)).toEqual({ day_from: "2026-10-06", day_to: "2026-10-06", cursor: "c2" });
    expect(screen.queryByRole("button", { name: "Yana ko'rsatish" })).toBeNull();
    // The figures are of everything the filters match, as the first page said: not of the last page read.
    expect(figures()).toEqual(["Savdolar soni2", som("Jami tushum53 500 so'm")]);
    // The seller met on the second page can be chosen too.
    expect([...(screen.getByLabelText("Kim sotdi") as HTMLSelectElement).options].map((option) => option.value)).toContain(OTHER_MEMBER);
  });

  it("says that nothing matched, and shows the server's refusal", async () => {
    let fail = false;
    const server = backend({ list: () => salesListBody([]), listFails: () => fail });
    renderScreen(<SalesScreen office />, { fetch: server.fetch, ...MANAGER });
    expect(await screen.findByText("Bunday savdo topilmadi.")).toBeTruthy();
    fail = true;
    fireEvent.change(screen.getByLabelText("Holati"), { target: { value: "posted" } });
    expect((await screen.findByRole("alert")).textContent).toContain("Serverda xatolik.");
    expect(screen.queryByText("Bunday savdo topilmadi.")).toBeNull();
  });
});

describe("the days of a list", () => {
  it("are a stretch in order, no longer than the server lists at once", () => {
    expect(readPeriod("2026-10-06", "2026-10-06")).toEqual({ from: "2026-10-06", to: "2026-10-06" });
    expect(readPeriod("2025-10-06", "2026-10-06")).toEqual({ from: "2025-10-06", to: "2026-10-06" });
    expect(MAX_SALES_DAYS).toBe(366);
    expect(readPeriod("2025-10-05", "2026-10-06")).toBeNull();
    expect(readPeriod("2026-10-07", "2026-10-06")).toBeNull();
    expect(readPeriod("", "2026-10-06")).toBeNull();
    expect(readPeriod("2026-10-06", "06.10.2026")).toBeNull();
  });
});

describe("one sale", () => {
  const view = (server: ReturnType<typeof fakeServer>, who: { role: "seller" | "manager" | "owner"; permissions?: string[] } = MANAGER) =>
    renderScreen(<SalesScreen saleId={SALE_ID} />, { fetch: server.fetch, ...who });
  const mayCancel = () => salesListBody([mine()], { may_cancel: true });

  it("is taken back only with a reason, by one request, and then stands as cancelled", async () => {
    const server = backend({ list: mayCancel });
    view(server);
    fireEvent.click(await screen.findByRole("button", { name: "Savdoni bekor qilish" }));
    expect(screen.getByText("Tovar omborga qaytadi, kassadagi kirim yozuvi ham bekor qilinadi. Sabab daftarda qoladi.")).toBeTruthy();
    // No reason, no request.
    fireEvent.click(within(screen.getByRole("group", { name: "Nima uchun bekor qilinmoqda?" })).getByRole("button", { name: "Savdoni bekor qilish" }));
    expect(screen.getByText("Sababni yozing: 1 dan 200 tagacha belgi.")).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "Nima uchun bekor qilinmoqda?" }), { target: { value: "   " } });
    fireEvent.click(within(screen.getByRole("group", { name: "Nima uchun bekor qilinmoqda?" })).getByRole("button", { name: "Savdoni bekor qilish" }));
    expect(server.writes()).toEqual([]);

    fireEvent.change(screen.getByRole("textbox", { name: "Nima uchun bekor qilinmoqda?" }), { target: { value: " xato  urilgan " } });
    fireEvent.click(within(screen.getByRole("group", { name: "Nima uchun bekor qilinmoqda?" })).getByRole("button", { name: "Savdoni bekor qilish" }));
    expect(await screen.findByText("Savdo bekor qilindi.")).toBeTruthy();
    expect(server.writes()).toHaveLength(1);
    expect(server.writes()[0]?.path).toBe(`${STOCK}/sales/${SALE_ID}/cancel`);
    expect(server.writes()[0]?.body).toEqual({ reason: "xato urilgan" });
    expect(server.writes()[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(screen.getByRole("heading", { name: /Naqd savdo № 3/ }).textContent).toBe("Naqd savdo № 3 Bekor qilingan");
    expect(fact("Bekor qilish sababi")).toBe("xato urilgan");
    // A cancelled sale cannot be cancelled again: the action is gone.
    expect(screen.queryByRole("button", { name: "Savdoni bekor qilish" })).toBeNull();
    // The list behind it is read again: its figures no longer hold this sale.
    await waitFor(() => expect(asked(server)).toHaveLength(2));
  });

  it("offers that only when the list's answer said this member may, whatever the role", async () => {
    // An owner by role, and the server says no (the right was taken from them): nothing is offered.
    const server = backend({ list: () => salesListBody([mine()], { may_cancel: false }) });
    view(server, { role: "owner" });
    await screen.findByRole("heading", { name: /Naqd savdo № 3/ });
    await waitFor(() => expect(asked(server)).toHaveLength(1));
    expect(screen.queryByRole("button", { name: "Savdoni bekor qilish" })).toBeNull();
    cleanup();
    // A seller by role whom the server lets: offered.
    view(backend({ list: mayCancel }), SELLER);
    expect(await screen.findByRole("button", { name: "Savdoni bekor qilish" })).toBeTruthy();
    cleanup();
    // And never while the list has not answered, or has failed.
    const failing = backend({ list: mayCancel, listFails: () => true });
    view(failing, { role: "owner" });
    await screen.findByRole("heading", { name: /Naqd savdo № 3/ });
    await waitFor(() => expect(asked(failing)).toHaveLength(1));
    expect(screen.queryByRole("button", { name: "Savdoni bekor qilish" })).toBeNull();
  });

  it("shows the server's words when the sale was already taken back by someone else", async () => {
    const server = backend({ list: mayCancel, cancel: () => refusal(409, "SALE_CANCELLED", "Bu savdo allaqachon bekor qilingan.") });
    view(server);
    fireEvent.click(await screen.findByRole("button", { name: "Savdoni bekor qilish" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Nima uchun bekor qilinmoqda?" }), { target: { value: "xato" } });
    fireEvent.click(within(screen.getByRole("group", { name: "Nima uchun bekor qilinmoqda?" })).getByRole("button", { name: "Savdoni bekor qilish" }));
    expect(await screen.findByText("Bu savdo allaqachon bekor qilingan.")).toBeTruthy();
    expect(screen.queryByText("Savdo bekor qilindi.")).toBeNull();
  });

  it("shows who sold it, how, and what of; and nothing of cost where the server sent none", async () => {
    const server = backend({ sale: () => ok(saleBody({ created_by: OTHER_MEMBER, seller_role: null, mine: false, method: "transfer", note: "ulgurji" })) });
    view(server);
    await screen.findByRole("heading", { name: /Naqd savdo № 3/ });
    expect(fact("Jami")).toBe(som("37 500 so'm"));
    expect(fact("To'lov usuli")).toBe("O'tkazma");
    expect(fact("Vaqt")).toBe("2026-yil 6-oktabr, 11:30");
    expect(fact("Kim sotdi")).toBe("Ishdan ketgan xodim");
    expect(fact("Izoh")).toBe("ulgurji");
    expect(shownOfSale()).not.toMatch(/tannarx|foyda/i);
    expect(screen.queryByText("Kassaga kirim bo'lib yozildi.")).toBeNull();
    expect(screen.getByRole("link", { name: "Naqd savdolar" }).getAttribute("href")).toBe("#/stock/sales");
  });

  it("is an unknown page when the server knows no such sale", async () => {
    view(backend({ sale: () => NOT_FOUND }));
    expect(await screen.findByText("Bosh sahifaga qaytish")).toBeTruthy();
  });
});

describe("where the stock names a cash sale", () => {
  const movement = (overrides: Record<string, unknown>) => ({
    id: "aaaaaaaa-0000-4000-8000-000000000001",
    seq: 2,
    kind: "sale",
    qty: "-2.5",
    on_hand_after: "5",
    reason: null,
    document: null,
    reverses_id: null,
    reversed: false,
    author_id: MEMBER,
    created_at: "2026-10-06T06:30:00+00:00",
    sale_total: 37500,
    ...overrides,
  });
  const item = (movements: unknown[]) =>
    fakeServer((sent) => {
      if (sent.path === `${STOCK}/settings`) {
        return ok(stockSettingsBody());
      }
      return sent.path === `${STOCK}/items/${ITEM_ID}` ? ok(stockItemBody()) : ok({ movements, next_cursor: null });
    });
  const moves = async () => within(await screen.findByRole("list", { name: "Harakatlar" })).getAllByRole("listitem");

  it("an item's movements name it 'Naqd savdo № N' and lead to its own page, never to the documents", async () => {
    const server = item([
      movement({ document: { id: SALE_ID, kind: "sale", number: 3 } }),
      // A sale on credit has no document, and a receipt has one of the documents' own kinds: as before.
      movement({ id: "aaaaaaaa-0000-4000-8000-000000000002", seq: 1, qty: "8", on_hand_after: "7.5", kind: "receipt", document: { id: SALE_ID, kind: "receipt", number: 7 } }),
    ]);
    renderScreen(<StockItemScreen itemId={ITEM_ID} host={{}} />, { fetch: server.fetch, ...SELLER });
    const [sold, received] = (await moves()) as [HTMLElement, HTMLElement];
    expect(sold.textContent).toBe("2026-yil 6-oktabr, 11:30Nima bo'ldiSotuv · Naqd savdo № 3Miqdor-2,5 kgKeyingi qoldiq5 kg");
    expect(within(sold).getByRole("link", { name: "Naqd savdo № 3" }).getAttribute("href")).toBe(`#/stock/sales/${SALE_ID}`);
    // Not the server's key, and not an address that answers 404 for a sale.
    expect(sold.textContent).not.toContain("sale");
    expect([...document.querySelectorAll("a")].map((link) => link.getAttribute("href")).filter((href) => href?.includes("documents"))).toEqual([]);
    expect(received.textContent).toContain("Kirim · Kirim № 7");
    expect(within(received).queryByRole("link")).toBeNull();
  });

  it("names it without a link for a member who may not open a sale", async () => {
    const server = item([movement({ document: { id: SALE_ID, kind: "sale", number: 3 } })]);
    renderScreen(<StockItemScreen itemId={ITEM_ID} host={{}} />, { fetch: server.fetch, role: "manager", permissions: ["stock.view"] });
    const [sold] = (await moves()) as [HTMLElement];
    expect(sold.textContent).toContain("Sotuv · Naqd savdo № 3");
    expect(within(sold).queryByRole("link")).toBeNull();
  });

  it("the stock's screen leads one who sells to the sale and to the day's sales, and nobody else", async () => {
    const server = backend();
    renderScreen(<StockScreen host={{}} />, { fetch: server.fetch, ...SELLER });
    await screen.findByText("Shakar");
    expect(screen.getByRole("link", { name: "Naqd savdo" }).getAttribute("href")).toBe("#/stock/sale");
    expect(screen.getByRole("link", { name: "Naqd savdolar" }).getAttribute("href")).toBe("#/stock/sales");
    cleanup();
    // By permission: a manager from whom selling for cash was taken is offered neither.
    renderScreen(<StockScreen host={{}} />, { fetch: server.fetch, role: "manager", permissions: ["stock.view", "stock.receive", "stock.sell.cancel"] });
    await screen.findByText("Shakar");
    expect(screen.queryByRole("link", { name: "Naqd savdo" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Naqd savdolar" })).toBeNull();
    expect(server.sent.filter((sent) => sent.path.includes("/sales"))).toEqual([]);
  });

  it("the report shows what sold by item, with how much of it was for cash, and the period's cash sales", async () => {
    const report = stockReportBody({
      sold: {
        items: [
          { item_id: ITEM_ID, name: "Shakar", unit: "kg", qty: "12.5", revenue: 187500, cost: 150000, margin: 37500, cash_qty: "2.5", cash_revenue: 37500 },
          // Sold at a loss: the margin is below zero, and is shown so.
          { item_id: OTHER_ITEM, name: "Choy", unit: "dona", qty: "2", revenue: 16000, cost: 18000, margin: -2000, cash_qty: "0", cash_revenue: 0 },
        ],
        more: true,
      },
      cash_sales: { count: 4, total: 91500, by_method: [{ method: "card", total: 16000 }, { method: "cash", total: 75500 }] },
    });
    const server = fakeServer((sent) => (sent.path === `${STOCK}/report` ? ok(report) : NOT_FOUND));
    const { container } = renderScreen(<StockReportScreen />, { fetch: server.fetch, ...MANAGER });
    const sold = within(await screen.findByRole("list", { name: "30 kun ichida sotilganlar" })).getAllByRole("listitem");
    expect(sold.map((row) => row.textContent)).toEqual([
      som("ShakarMiqdor12,5 kgTushum187 500 so'mTannarx150 000 so'mFoyda37 500 so'mShundan naqd savdoda2,5 kgNaqd savdodan tushum37 500 so'm"),
      som("ChoyMiqdor2 donaTushum16 000 so'mTannarx18 000 so'mFoyda-2 000 so'mShundan naqd savdoda0 donaNaqd savdodan tushum0 so'm"),
    ]);
    expect(within(sold[0] as HTMLElement).getByRole("link", { name: "Shakar" }).getAttribute("href")).toBe(`#/stock/items/${ITEM_ID}`);
    expect(screen.getByRole("heading", { name: "30 kun ichidagi naqd savdolar" })).toBeTruthy();
    expect([...container.querySelectorAll(".figure")].map((figure) => figure.textContent).slice(-4)).toEqual([
      "Savdolar soni4",
      som("Jami tushum91 500 so'm"),
      som("Karta16 000 so'm"),
      som("Naqd75 500 so'm"),
    ]);
    // Two lists are cut at fifty, and each says so.
    expect(screen.getAllByText("Ro'yxatda faqat dastlabki 50 tasi ko'rsatilgan.")).toHaveLength(2);
  });

  it("the report says when nothing sold, and is still for those who may see cost alone", async () => {
    const server = fakeServer((sent) => (sent.path === `${STOCK}/report` ? ok(stockReportBody()) : NOT_FOUND));
    renderScreen(<StockReportScreen />, { fetch: server.fetch, ...MANAGER });
    expect(await screen.findByText("Bu davrda hisobdagi tovar sotilmagan.")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "30 kun ichida sotilganlar" })).toBeNull();
    cleanup();
    const closed = fakeServer(() => ok(stockReportBody()));
    renderScreen(<StockReportScreen />, { fetch: closed.fetch, role: "seller", permissions: ["stock.view", "stock.sell", "stock.sell.cancel"] });
    expect(await screen.findByText("Bosh sahifaga qaytish")).toBeTruthy();
    expect(closed.sent).toEqual([]);
  });
});

describe("the sale's calls", () => {
  const client = (server: ReturnType<typeof fakeServer>) => stockOf(createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "t" } }).shop(SHOP_ID));
  const failure = async (promise: Promise<unknown>): Promise<ApiError> => {
    try {
      await promise;
    } catch (error) {
      return error as ApiError;
    }
    throw new Error("expected a failure");
  };

  it("always say the price, and the note only when there is one", () => {
    expect(requestBody({ lines: [{ itemId: ITEM_ID, qty: "2.5", price: 15000 }], method: "transfer", note: null })).toEqual({
      lines: [{ item_id: ITEM_ID, qty: "2.5", price: 15000 }],
      method: "transfer",
    });
    expect(requestBody({ lines: [], method: "cash", note: "x" })).toEqual({ lines: [], method: "cash", note: "x" });
  });

  it("never ask for one's own sales and for a seller's together", async () => {
    const server = fakeServer(() => ok(salesListBody([])));
    await client(server).sales({ mine: true, sellerId: OTHER_MEMBER, status: "", itemId: "" });
    expect(server.sent[0]?.query).toEqual({ mine: "true" });
    await client(server).sales({ mine: false, sellerId: OTHER_MEMBER, dayFrom: "2026-10-01", dayTo: "2026-10-06", status: "posted", limit: 1 });
    expect(server.sent[1]?.query).toEqual({ seller_id: OTHER_MEMBER, day_from: "2026-10-01", day_to: "2026-10-06", status: "posted", limit: "1" });
  });

  it("read cost as absent, never as a zero, when the server sent none", async () => {
    const plain = await client(fakeServer(() => ok(saleBody()))).sale(SALE_ID);
    expect(plain.cost).toBeUndefined();
    expect("cost" in plain).toBe(false);
    expect(plain.lines.every((line) => !("cost" in line))).toBe(true);
    const costed = await client(fakeServer(() => ok(costedSaleBody()))).sale(SALE_ID);
    expect(costed.cost).toEqual({ total: 30000, margin: 7500, complete: true });
    expect(costed.lines[0]?.cost).toEqual({ currency: "UZS", total: 30000, margin: 7500 });
    // A list says nothing of whether a line was counted: unknown, not "no".
    const page = await client(fakeServer(() => ok(salesListBody([saleRowBody()], { may_cancel: true })))).sales({});
    expect(page.items[0]?.lines[0]?.counted).toBeNull();
    expect(page.mayCancel).toBe(true);
  });

  it("refuse an answer that is not a cash sale: another currency, an unknown state or way of paying", async () => {
    for (const wrong of [{ currency: "USD" }, { status: "draft" }, { method: "barter" }, { total: 1.5 }, { lines: [saleLineBody({ qty: "two" })] }]) {
      const error = await failure(client(fakeServer(() => ok(saleBody(wrong)))).sale(SALE_ID));
      expect(error.code, JSON.stringify(wrong)).toBe(BAD_RESPONSE);
    }
    expect((await failure(client(fakeServer(() => ok({ ...saleBody(), warnings: [{ kind: "negative", item: ITEM_ID, name: "Shakar", on_hand: "few" }] }))).recordSale({ lines: [], method: "cash" }, "key-0000-0001"))).code).toBe(BAD_RESPONSE);
    expect((await failure(client(fakeServer(() => ok(salesListBody([], { may_cancel: "yes" })))).sales({}))).code).toBe(BAD_RESPONSE);
  });

  it("send a write with its key and the reason of a cancellation", async () => {
    const server = fakeServer((sent) => (sent.path.endsWith("/cancel") ? ok(saleBody({ status: "cancelled" })) : ok({ ...saleBody(), warnings: [] }, 201)));
    await client(server).recordSale({ lines: [{ itemId: ITEM_ID, qty: "1", price: 15000 }], method: "cash" }, "key-0000-0001");
    await client(server).cancelSale(SALE_ID, "xato", "key-0000-0002");
    expect(server.sent.map((sent) => [sent.method, sent.path, sent.headers["Idempotency-Key"], sent.body])).toEqual([
      ["POST", `${STOCK}/sales`, "key-0000-0001", { lines: [{ item_id: ITEM_ID, qty: "1", price: 15000 }], method: "cash" }],
      ["POST", `${STOCK}/sales/${SALE_ID}/cancel`, "key-0000-0002", { reason: "xato" }],
    ]);
  });
});
