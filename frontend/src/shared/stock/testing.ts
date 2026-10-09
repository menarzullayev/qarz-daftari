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
