import { SHOP_BASE } from "../../testing/fakeServer";

/** Bodies of the stock's API as the server writes them, for the tests of its screens. */

export const STOCK = `${SHOP_BASE}/stock`;
export const SUPPLIERS = `${SHOP_BASE}/suppliers`;

export const ITEM_ID = "44444444-4444-4444-8444-444444444441";
export const OTHER_ITEM = "44444444-4444-4444-8444-444444444442";
export const SUPPLIER_ID = "77777777-7777-4777-8777-777777777771";
export const DOCUMENT_ID = "88888888-8888-4888-8888-888888888881";
export const ENTRY_ID = "99999999-9999-4999-8999-999999999991";
export const MEMBER = "33333333-3333-4333-8333-333333333333";

export const EAN = "4006381333931";
export const UNKNOWN_EAN = "73513537";

/** GET stock/settings: sales may go below zero, so'm only. */
export function stockSettingsBody(overrides: Record<string, unknown> = {}) {
  return {
    refuse_negative: false,
    currencies: ["UZS"],
    units: [
      { key: "dona", label: { uz: "dona", ru: "шт" }, weighed: false },
      { key: "kg", label: { uz: "kg", ru: "кг" }, weighed: true },
    ],
    write_off_reasons: [
      { key: "damaged", label: { uz: "Shikastlangan", ru: "Повреждён" } },
      { key: "expired", label: { uz: "Muddati o'tgan", ru: "Истёк срок" } },
    ],
    document_kinds: ["receipt", "customer_return", "supplier_return", "write_off", "stocktake"],
    ...overrides,
  };
}

/** An item as a seller is sent it: no cost. */
export function stockItemBody(overrides: Record<string, unknown> = {}) {
  return {
    id: ITEM_ID,
    name: "Shakar",
    unit: "kg",
    price: 15000,
    status: "active",
    learned: false,
    tracked: true,
    on_hand: "7.5",
    low_stock: null,
    low: false,
    barcodes: [EAN],
    last_sale_at: null,
    ...overrides,
  };
}

/** The same with what a member who may see cost is sent. */
export function costedItemBody(overrides: Record<string, unknown> = {}) {
  return stockItemBody({ cost: { currency: "UZS", average: 12000, value: 90000, margin: 3000 }, ...overrides });
}

export function documentBody(overrides: Record<string, unknown> = {}) {
  return {
    id: DOCUMENT_ID,
    kind: "receipt",
    number: 7,
    status: "posted",
    doc_date: "2026-10-06",
    supplier: null,
    customer: null,
    reason: null,
    note: null,
    created_by: MEMBER,
    created_at: "2026-10-06T05:00:00+00:00",
    posted_at: "2026-10-06T05:00:00+00:00",
    cancelled_at: null,
    cancel_reason: null,
    currency: "UZS",
    total: 24000,
    paid: 24000,
    ledger_entry_id: null,
    lines: [{ line_no: 1, item: { id: ITEM_ID, name: "Shakar", unit: "kg" }, qty: "2", unit_cost: 12000, line_total: 24000 }],
    ...overrides,
  };
}

export function supplierBody(overrides: Record<string, unknown> = {}) {
  return {
    id: SUPPLIER_ID,
    name: "Baraka ulgurji",
    phone: "+998901112233",
    note: null,
    status: "active",
    balances: [{ currency: "UZS", balance: 500000 }],
    ...overrides,
  };
}

export function supplierEntryBody(overrides: Record<string, unknown> = {}) {
  return {
    id: ENTRY_ID,
    seq: 1,
    kind: "payment",
    amount: 100000,
    currency: "UZS",
    note: null,
    reverses_id: null,
    reversed: false,
    document: null,
    in_cash_book: false,
    author_id: MEMBER,
    created_at: "2026-10-06T05:10:00+00:00",
    ...overrides,
  };
}

/** `count` suppliers named "Ta'minotchi 01", "Ta'minotchi 02", ...: more than one page of a choice. */
export function manySuppliers(count: number, overrides: Record<string, unknown> = {}) {
  return Array.from({ length: count }, (_, index) =>
    supplierBody({
      id: `77777777-7777-4777-8777-${String(index + 1).padStart(12, "0")}`,
      name: `Ta'minotchi ${String(index + 1).padStart(2, "0")}`,
      ...overrides,
    }),
  );
}

/**
 * GET suppliers as the server answers it over `all`: one status, narrowed by a part of the name, a page
 * at a time with a cursor of its own (`SupplierService.list`). A list filled from its first page alone
 * cannot find what this keeps for a later page.
 */
export function supplierListBody(all: readonly Record<string, unknown>[], query: Record<string, string>) {
  const status = query["status"] ?? "active";
  const part = (query["q"] ?? "").toLowerCase();
  const limit = Number(query["limit"] ?? "50");
  const matching = all.filter((supplier) => supplier["status"] === status && String(supplier["name"]).toLowerCase().includes(part));
  const start = query["cursor"] === undefined ? 0 : Number(query["cursor"].slice(1));
  return {
    suppliers: matching.slice(start, start + limit),
    totals: [],
    next_cursor: start + limit < matching.length ? `c${start + limit}` : null,
  };
}

export const SALE_ID = "88888888-8888-4888-8888-8888888888a1";
export const OTHER_SALE = "88888888-8888-4888-8888-8888888888a2";
export const OTHER_MEMBER = "33333333-3333-4333-8333-333333333334";

/** One line of a sale as a seller is sent it: no cost. */
export function saleLineBody(overrides: Record<string, unknown> = {}) {
  return {
    line_no: 1,
    item: { id: ITEM_ID, name: "Shakar", unit: "kg" },
    qty: "2.5",
    price: 15000,
    line_total: 37500,
    counted: true,
    ...overrides,
  };
}

/** A sale in full (POST stock/sales without `warnings`, GET stock/sales/{id}) as a seller is sent it. */
export function saleBody(overrides: Record<string, unknown> = {}) {
  return {
    id: SALE_ID,
    number: 3,
    status: "posted",
    day: "2026-10-06",
    created_at: "2026-10-06T06:30:00+00:00",
    created_by: MEMBER,
    seller_role: "seller",
    mine: true,
    method: "cash",
    currency: "UZS",
    total: 37500,
    note: null,
    cancelled_at: null,
    cancel_reason: null,
    in_cash_book: false,
    lines: [saleLineBody()],
    ...overrides,
  };
}

/** The same with what a member who may see cost is sent: on each line, and on the sale. */
export function costedSaleBody(overrides: Record<string, unknown> = {}) {
  return saleBody({
    lines: [saleLineBody({ cost: { currency: "UZS", total: 30000, margin: 7500 } })],
    cost: { total: 30000, margin: 7500, complete: true },
    ...overrides,
  });
}

/** A sale as a list writes it: no `in_cash_book`, no cost, and `counted` null on every line. */
export function saleRowBody(overrides: Record<string, unknown> = {}) {
  return { ...saleBody(), in_cash_book: undefined, lines: [saleLineBody({ counted: null })], ...overrides };
}

/** GET stock/sales: one day, its sales, and what those that stand came to. */
export function salesListBody(sales: readonly Record<string, unknown>[], overrides: Record<string, unknown> = {}) {
  const standing = sales.filter((sale) => sale["status"] !== "cancelled");
  const byMethod = new Map<string, number>();
  for (const sale of standing) {
    byMethod.set(String(sale["method"]), (byMethod.get(String(sale["method"])) ?? 0) + Number(sale["total"]));
  }
  return {
    day_from: "2026-10-06",
    day_to: "2026-10-06",
    sales,
    totals: {
      count: standing.length,
      total: standing.reduce((sum, sale) => sum + Number(sale["total"]), 0),
      by_method: ["cash", "card", "transfer"].filter((method) => byMethod.has(method)).map((method) => ({ method, total: byMethod.get(method) })),
    },
    may_cancel: false,
    next_cursor: null,
    ...overrides,
  };
}

/** GET stock/report, for a member who may see cost: nobody else is answered. */
export function stockReportBody(overrides: Record<string, unknown> = {}) {
  return {
    days: 30,
    totals: {
      items: 12,
      low: 2,
      cost: [
        { currency: "UZS", value: 900000 },
        { currency: "USD", value: 45000 },
      ],
      selling: 1500000,
      margin: { selling: 1200000, cost: 900000, margin: 300000 },
    },
    not_sold: { items: [costedItemBody({ last_sale_at: null })], more: true },
    sold_below_cost: {
      sales: [
        { item_id: ITEM_ID, name: "Shakar", unit: "kg", qty: "2", sale_total: 20000, cost_total: 24000, loss: 4000, created_at: "2026-10-05T06:00:00+00:00" },
      ],
      more: false,
    },
    low_stock: { items: [], more: false },
    sold: { items: [], more: false },
    cash_sales: { count: 0, total: 0, by_method: [] },
    ...overrides,
  };
}
