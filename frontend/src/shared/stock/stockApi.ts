import { reading, type ShopApi } from "../api";
import type { Currency } from "../money";
import { isRole, type Role } from "../navigation";
import { STOCK_DOCUMENT_KINDS, type StockDocumentKind } from "../workspace/routes";

/**
 * The stock, its documents and the suppliers of a shop (the expansion's module I). Built on the shop
 * API's `send` in a module that is loaded apart, so none of it is part of the first load. Shapes follow
 * backend/src/qarz/application/stock.py, stock_documents.py and suppliers.py.
 *
 * Quantities are the API's decimal strings ("7.5", "-2", up to three decimals) and stay strings here:
 * nothing is ever computed from them as a fraction. Money is a whole number of the currency's minor
 * unit (so'm, or cents) with its currency beside it; two currencies are never added.
 *
 * What goods cost is sensitive. The server leaves the cost figures out of an answer unless the member
 * may see them: here they are then absent (`cost`, `total`, `unitCost` undefined), never a zero.
 */

const { record, text, textOrNull, whole, wholeOrNull, flag, list } = reading;

export { STOCK_DOCUMENT_KINDS, type StockDocumentKind };

const QTY = /^-?\d{1,12}(?:\.\d{1,3})?$/;

function qty(value: unknown): string {
  const raw = text(value);
  if (!QTY.test(raw)) {
    throw new RangeError("not a quantity");
  }
  return raw;
}

function qtyOrNull(value: unknown): string | null {
  return value === null || value === undefined ? null : qty(value);
}

function currency(value: unknown): Currency {
  if (value !== "UZS" && value !== "USD") {
    throw new RangeError("not a currency");
  }
  return value;
}

function currencyOrNull(value: unknown): Currency | null {
  return value === null || value === undefined ? null : currency(value);
}

/**
 * A name the server gives in every language of the product, by its tag (the names of units and of
 * write-off reasons). The reader is shown their own (`labelIn`); a language the answer lacks, or has
 * empty, reads Uzbek, like every other text.
 */
export type Labelled = { key: string; label: { uz: string } & Partial<Record<string, string>> };

export function labelIn(label: Labelled["label"], language: string): string {
  return label[language] || label.uz;
}
export type StockUnit = Labelled & { weighed: boolean };

export type StockSettings = {
  refuseNegative: boolean;
  /** The currencies the shop buys in: so'm, and dollars when the shop works in them. */
  currencies: Currency[];
  units: StockUnit[];
  writeOffReasons: Labelled[];
  /**
   * The shop keeps a cash book: money paid to a supplier or handed back to a customer is then also an
   * entry of it, and the form asks how it was paid. False whenever the server does not say.
   */
  cashBook: boolean;
};

/** How money was paid, for the cash book. */
export const PAYMENT_METHODS = ["cash", "card", "transfer"] as const;
export type PaymentMethod = (typeof PAYMENT_METHODS)[number];

function labelled(value: unknown): Labelled {
  const body = record(value);
  const label = record(body["label"]);
  const names: Labelled["label"] = { uz: text(label["uz"]) };
  for (const [language, name] of Object.entries(label)) {
    if (typeof name === "string") {
      names[language] = name;
    }
  }
  return { key: text(body["key"]), label: names };
}

function stockSettings(value: unknown): StockSettings {
  const body = record(value);
  return {
    refuseNegative: flag(body["refuse_negative"]),
    currencies: list(body["currencies"], currency),
    units: list(body["units"], (element) => ({ ...labelled(element), weighed: flag(record(element)["weighed"]) })),
    writeOffReasons: list(body["write_off_reasons"], labelled),
    cashBook: body["cash_book"] === true,
  };
}

/**
 * What an item's stock is worth. `average` is the cost of one unit, `value` of everything on hand,
 * `margin` the selling price less the average; each is null when there is nothing to say yet.
 */
export type ItemCost = { currency: Currency | null; average: number | null; value: number; margin: number | null };

export type StockItem = {
  id: string;
  name: string;
  unit: string;
  /** The selling price in whole so'm. */
  price: number;
  status: string;
  learned: boolean;
  tracked: boolean;
  onHand: string;
  lowStock: string | null;
  low: boolean;
  barcodes: string[];
  lastSaleAt: string | null;
  cost?: ItemCost;
};

function itemCost(value: unknown): ItemCost {
  const body = record(value);
  return {
    currency: currencyOrNull(body["currency"]),
    average: wholeOrNull(body["average"]),
    value: whole(body["value"]),
    margin: wholeOrNull(body["margin"]),
  };
}

function stockItem(value: unknown): StockItem {
  const body = record(value);
  const item: StockItem = {
    id: text(body["id"]),
    name: text(body["name"]),
    unit: text(body["unit"]),
    price: whole(body["price"]),
    status: text(body["status"]),
    learned: flag(body["learned"]),
    tracked: flag(body["tracked"]),
    onHand: qty(body["on_hand"]),
    lowStock: qtyOrNull(body["low_stock"]),
    low: flag(body["low"]),
    barcodes: list(body["barcodes"], text),
    lastSaleAt: textOrNull(body["last_sale_at"]),
  };
  if (body["cost"] !== undefined && body["cost"] !== null) {
    item.cost = itemCost(body["cost"]);
  }
  return item;
}

export type DocumentRef = { id: string; kind: string; number: number };

function documentRef(value: unknown): DocumentRef | null {
  if (value === null || value === undefined) {
    return null;
  }
  const body = record(value);
  return { id: text(body["id"]), kind: text(body["kind"]), number: whole(body["number"]) };
}

export type Movement = {
  id: string;
  seq: number;
  kind: string;
  /** Signed: above zero came in, below zero went out. */
  qty: string;
  onHandAfter: string;
  reason: string | null;
  document: DocumentRef | null;
  reversesId: string | null;
  reversed: boolean;
  authorId: string;
  createdAt: string;
  /** What the line of a sale was sold for, in whole so'm. */
  saleTotal?: number;
  cost?: { currency: Currency | null; unitCost: number | null; total: number | null };
};

function movement(value: unknown): Movement {
  const body = record(value);
  const row: Movement = {
    id: text(body["id"]),
    seq: whole(body["seq"]),
    kind: text(body["kind"]),
    qty: qty(body["qty"]),
    onHandAfter: qty(body["on_hand_after"]),
    reason: textOrNull(body["reason"]),
    document: documentRef(body["document"]),
    reversesId: textOrNull(body["reverses_id"]),
    reversed: flag(body["reversed"]),
    authorId: text(body["author_id"]),
    createdAt: text(body["created_at"]),
  };
  if (body["sale_total"] !== undefined && body["sale_total"] !== null) {
    row.saleTotal = whole(body["sale_total"]);
  }
  if (body["cost"] !== undefined && body["cost"] !== null) {
    const cost = record(body["cost"]);
    row.cost = {
      currency: currencyOrNull(cost["currency"]),
      unitCost: wholeOrNull(cost["unit_cost"]),
      total: wholeOrNull(cost["total"]),
    };
  }
  return row;
}

export type Named = { id: string; name: string };

function named(value: unknown): Named | null {
  if (value === null || value === undefined) {
    return null;
  }
  const body = record(value);
  return { id: text(body["id"]), name: textOrNull(body["name"]) ?? "" };
}

export type DocumentStatus = "draft" | "posted" | "cancelled";
export const DOCUMENT_STATUSES: readonly DocumentStatus[] = ["draft", "posted", "cancelled"];

/** Money of a document; absent as a whole for a member who may not see what goods cost. */
export type DocumentMoney = { currency: Currency; total: number; paid: number };

export type DocumentSummary = {
  id: string;
  kind: StockDocumentKind;
  number: number;
  status: DocumentStatus;
  docDate: string;
  supplier: Named | null;
  customer: Named | null;
  reason: string | null;
  note: string | null;
  createdBy: string;
  createdAt: string;
  postedAt: string | null;
  cancelledAt: string | null;
  cancelReason: string | null;
  money?: DocumentMoney;
};

export type DocumentLine = {
  lineNo: number;
  item: { id: string; name: string; unit: string };
  qty: string;
  /** A stocktake only: what the books hold (for a draft, right now) and the count less that. */
  expected?: string | null;
  difference?: string | null;
  unitCost?: number;
  lineTotal?: number;
};

export type StockDocument = DocumentSummary & { lines: DocumentLine[] };

function documentSummary(value: unknown): DocumentSummary {
  const body = record(value);
  const kind = STOCK_DOCUMENT_KINDS.find((known) => known === body["kind"]);
  const status = DOCUMENT_STATUSES.find((known) => known === body["status"]);
  if (!kind || !status) {
    throw new RangeError("not a stock document");
  }
  const summary: DocumentSummary = {
    id: text(body["id"]),
    kind,
    number: whole(body["number"]),
    status,
    docDate: text(body["doc_date"]),
    supplier: named(body["supplier"]),
    customer: named(body["customer"]),
    reason: textOrNull(body["reason"]),
    note: textOrNull(body["note"]),
    createdBy: text(body["created_by"]),
    createdAt: text(body["created_at"]),
    postedAt: textOrNull(body["posted_at"]),
    cancelledAt: textOrNull(body["cancelled_at"]),
    cancelReason: textOrNull(body["cancel_reason"]),
  };
  if (body["total"] !== undefined && body["total"] !== null) {
    summary.money = { currency: currency(body["currency"]), total: whole(body["total"]), paid: whole(body["paid"]) };
  }
  return summary;
}

function documentLine(value: unknown): DocumentLine {
  const body = record(value);
  const item = record(body["item"]);
  const line: DocumentLine = {
    lineNo: whole(body["line_no"]),
    item: { id: text(item["id"]), name: text(item["name"]), unit: text(item["unit"]) },
    qty: qty(body["qty"]),
  };
  if ("expected" in body) {
    line.expected = qtyOrNull(body["expected"]);
    line.difference = qtyOrNull(body["difference"]);
  }
  if (body["unit_cost"] !== undefined && body["unit_cost"] !== null) {
    line.unitCost = whole(body["unit_cost"]);
    line.lineTotal = whole(body["line_total"]);
  }
  return line;
}

function stockDocument(value: unknown): StockDocument {
  return { ...documentSummary(value), lines: list(record(value)["lines"], documentLine) };
}

/** A line to save: an item of the catalog, or (on a receipt only) a good met for the first time. */
export type NewDocumentLine = {
  qty: string;
  unitCost?: number;
} & ({ itemId: string } | { newItem: { name: string; unit: string; price: number; barcode: string | null } });

export type NewDocument = {
  kind: StockDocumentKind;
  docDate?: string | null;
  supplierId?: string | null;
  customerId?: string | null;
  currency?: Currency | null;
  paid?: number | null;
  /** How what was paid was paid; said only while the shop keeps a cash book. */
  method?: PaymentMethod | null;
  reason?: string | null;
  note?: string | null;
  lines: readonly NewDocumentLine[];
};

/** The request body of a document. A key that does not apply is left out: the server refuses extras. */
export function documentBody(document: NewDocument): Record<string, unknown> {
  const body: Record<string, unknown> = {
    kind: document.kind,
    lines: document.lines.map((line) => {
      const row: Record<string, unknown> = { qty: line.qty };
      if ("itemId" in line) {
        row["item_id"] = line.itemId;
      } else {
        const added: Record<string, unknown> = { name: line.newItem.name, unit: line.newItem.unit, price: line.newItem.price };
        if (line.newItem.barcode !== null) {
          added["barcode"] = line.newItem.barcode;
        }
        row["new_item"] = added;
      }
      if (line.unitCost !== undefined) {
        row["unit_cost"] = line.unitCost;
      }
      return row;
    }),
  };
  const optional: readonly (readonly [string, unknown])[] = [
    ["doc_date", document.docDate],
    ["supplier_id", document.supplierId],
    ["customer_id", document.customerId],
    ["currency", document.currency],
    ["paid", document.paid],
    ["method", document.method],
    ["reason", document.reason],
    ["note", document.note],
  ];
  for (const [name, value] of optional) {
    if (value !== undefined && value !== null && value !== "") {
      body[name] = value;
    }
  }
  return body;
}

/** Above zero the shop owes the supplier; below zero it paid ahead. */
export type SupplierBalance = { currency: Currency; balance: number };

export type Supplier = {
  id: string;
  name: string;
  phone: string | null;
  note: string | null;
  status: string;
  balances: SupplierBalance[];
};

export type SupplierEntry = {
  id: string;
  seq: number;
  kind: string;
  amount: number;
  currency: Currency;
  note: string | null;
  reversesId: string | null;
  reversed: boolean;
  document: DocumentRef | null;
  inCashBook: boolean;
  authorId: string;
  createdAt: string;
};

export type SupplierList = {
  suppliers: Supplier[];
  /** What the shop owes all of its suppliers together, one figure for each currency. */
  totals: { currency: Currency; owed: number }[];
  nextCursor: string | null;
};

export type SupplierAccount = { supplier: Supplier; entries: SupplierEntry[]; nextCursor: string | null };

function supplier(value: unknown): Supplier {
  const body = record(value);
  return {
    id: text(body["id"]),
    name: text(body["name"]),
    phone: textOrNull(body["phone"]),
    note: textOrNull(body["note"]),
    status: text(body["status"]),
    balances: list(body["balances"], (element) => {
      const row = record(element);
      return { currency: currency(row["currency"]), balance: whole(row["balance"]) };
    }),
  };
}

function supplierEntry(value: unknown): SupplierEntry {
  const body = record(value);
  return {
    id: text(body["id"]),
    seq: whole(body["seq"]),
    kind: text(body["kind"]),
    amount: whole(body["amount"]),
    currency: currency(body["currency"]),
    note: textOrNull(body["note"]),
    reversesId: textOrNull(body["reverses_id"]),
    reversed: flag(body["reversed"]),
    document: documentRef(body["document"]),
    inCashBook: flag(body["in_cash_book"]),
    authorId: text(body["author_id"]),
    createdAt: text(body["created_at"]),
  };
}

export type SupplierInput = { name: string; phone: string | null; note: string | null };

function supplierBody(input: SupplierInput): Record<string, unknown> {
  const body: Record<string, unknown> = { name: input.name };
  if (input.phone !== null) {
    body["phone"] = input.phone;
  }
  if (input.note !== null) {
    body["note"] = input.note;
  }
  return body;
}

/**
 * A sale for cash, without a customer: goods lines at the counter, money in, counted stock out. In the
 * books it is a document of a kind of its own with routes of its own: the lists of documents never
 * hold one, and the documents' routes do not answer for it.
 */
export const SALE_KIND = "sale";

export type SaleStatus = "posted" | "cancelled";
export const SALE_STATUSES: readonly SaleStatus[] = ["posted", "cancelled"];

/** What a sold line cost and earned; a part is null where the cost is not known in so'm. */
export type SaleLineCost = { currency: Currency | null; total: number | null; margin: number | null };

export type SaleLine = {
  lineNo: number;
  item: { id: string; name: string; unit: string };
  qty: string;
  /** What one unit was sold for, and what the line came to, in whole so'm. */
  price: number;
  lineTotal: number;
  /** False: the item is not counted, and the line took nothing out of the stock. Null: a list does not say. */
  counted: boolean | null;
  /** Absent for a member who may not see what goods cost. */
  cost?: SaleLineCost;
};

export type SaleSummary = {
  id: string;
  number: number;
  status: SaleStatus;
  /** The Tashkent day of the sale, "YYYY-MM-DD". */
  day: string;
  createdAt: string;
  createdBy: string;
  /** The role of who sold; null for a member who has since left the shop. */
  sellerRole: Role | null;
  mine: boolean;
  method: PaymentMethod;
  total: number;
  note: string | null;
  cancelledAt: string | null;
  cancelReason: string | null;
  lines: SaleLine[];
};

/** A sale in full. `cost` is absent, never a zero, for a member who may not see what goods cost. */
export type Sale = SaleSummary & {
  inCashBook: boolean;
  /** `complete` false: a counted line has no cost in so'm, and the margin is of the others alone. */
  cost?: { total: number; margin: number | null; complete: boolean };
};

/** What the seller is told about a sale that was saved. "below_cost" comes only to those who see cost. */
export type SaleWarning =
  | { kind: "negative"; itemId: string; name: string; onHand: string }
  | { kind: "below_cost"; itemId: string; name: string };

export type RecordedSale = Sale & { warnings: SaleWarning[] };

/** What the sales that stand came to; a cancelled one is in none of the figures. */
export type SaleTotals = { count: number; total: number; byMethod: { method: PaymentMethod; total: number }[] };

export type SalesPage = {
  items: SaleSummary[];
  nextCursor: string | null;
  dayFrom: string;
  dayTo: string;
  /** Of every sale the filters match, not of this page alone. */
  totals: SaleTotals;
  /** Whether the member may take a sale back: the one thing that offers the action. */
  mayCancel: boolean;
};

export type NewSaleLine = { itemId: string; qty: string; price: number };
export type NewSale = { lines: readonly NewSaleLine[]; method: PaymentMethod; note?: string | null };

export type SalesFilter = {
  dayFrom?: string | undefined;
  dayTo?: string | undefined;
  itemId?: string | undefined;
  sellerId?: string | undefined;
  /** The caller's own sales; never sent together with a seller. */
  mine?: boolean | undefined;
  status?: SaleStatus | "" | undefined;
  cursor?: string | null | undefined;
  limit?: number | undefined;
};

function paymentMethod(value: unknown): PaymentMethod {
  const method = PAYMENT_METHODS.find((known) => known === value);
  if (!method) {
    throw new RangeError("not a payment method");
  }
  return method;
}

function saleTotals(value: unknown): SaleTotals {
  const body = record(value);
  return {
    count: whole(body["count"]),
    total: whole(body["total"]),
    byMethod: list(body["by_method"], (element) => {
      const row = record(element);
      return { method: paymentMethod(row["method"]), total: whole(row["total"]) };
    }),
  };
}

function saleLine(value: unknown): SaleLine {
  const body = record(value);
  const item = record(body["item"]);
  const line: SaleLine = {
    lineNo: whole(body["line_no"]),
    item: { id: text(item["id"]), name: text(item["name"]), unit: text(item["unit"]) },
    qty: qty(body["qty"]),
    price: whole(body["price"]),
    lineTotal: whole(body["line_total"]),
    counted: typeof body["counted"] === "boolean" ? body["counted"] : null,
  };
  if (body["cost"] !== undefined && body["cost"] !== null) {
    const cost = record(body["cost"]);
    line.cost = { currency: currencyOrNull(cost["currency"]), total: wholeOrNull(cost["total"]), margin: wholeOrNull(cost["margin"]) };
  }
  return line;
}

function saleSummary(value: unknown): SaleSummary {
  const body = record(value);
  const status = SALE_STATUSES.find((known) => known === body["status"]);
  // A sale is in so'm, as selling prices are: anything else is not an answer this client can show.
  if (!status || body["currency"] !== "UZS") {
    throw new RangeError("not a cash sale");
  }
  return {
    id: text(body["id"]),
    number: whole(body["number"]),
    status,
    day: text(body["day"]),
    createdAt: text(body["created_at"]),
    createdBy: text(body["created_by"]),
    sellerRole: isRole(body["seller_role"]) ? body["seller_role"] : null,
    mine: flag(body["mine"]),
    method: paymentMethod(body["method"]),
    total: whole(body["total"]),
    note: textOrNull(body["note"]),
    cancelledAt: textOrNull(body["cancelled_at"]),
    cancelReason: textOrNull(body["cancel_reason"]),
    lines: list(body["lines"], saleLine),
  };
}

function sale(value: unknown): Sale {
  const body = record(value);
  const read: Sale = { ...saleSummary(value), inCashBook: flag(body["in_cash_book"]) };
  if (body["cost"] !== undefined && body["cost"] !== null) {
    const cost = record(body["cost"]);
    read.cost = { total: whole(cost["total"]), margin: wholeOrNull(cost["margin"]), complete: flag(cost["complete"]) };
  }
  return read;
}

function recordedSale(value: unknown): RecordedSale {
  const warnings: SaleWarning[] = [];
  for (const element of list(record(value)["warnings"], record)) {
    const named = { itemId: text(element["item"]), name: text(element["name"]) };
    if (element["kind"] === "negative") {
      warnings.push({ kind: "negative", ...named, onHand: qty(element["on_hand"]) });
    } else if (element["kind"] === "below_cost") {
      warnings.push({ kind: "below_cost", ...named });
    }
    // A kind this client does not know yet is left out: the sale is saved either way.
  }
  return { ...sale(value), warnings };
}

function salesPage(value: unknown): SalesPage {
  const body = record(value);
  return {
    dayFrom: text(body["day_from"]),
    dayTo: text(body["day_to"]),
    items: list(body["sales"], saleSummary),
    totals: saleTotals(body["totals"]),
    mayCancel: flag(body["may_cancel"]),
    nextCursor: textOrNull(body["next_cursor"]),
  };
}

/** The request body of a sale. The price is always said: what the seller saw is what is saved. */
export function saleBody(input: NewSale): Record<string, unknown> {
  const body: Record<string, unknown> = {
    lines: input.lines.map((line) => ({ item_id: line.itemId, qty: line.qty, price: line.price })),
    method: input.method,
  };
  if (input.note !== undefined && input.note !== null && input.note !== "") {
    body["note"] = input.note;
  }
  return body;
}

export type CostTotal ={ currency: Currency; value: number };

export type StockReport = {
  days: number;
  totals: {
    items: number;
    low: number;
    /** What the stock cost, one figure for each currency; never added together. */
    cost: CostTotal[];
    /** Everything on hand at its selling price, in whole so'm. */
    selling: number;
    /** Of the items whose cost is kept in so'm: their selling value, their cost, and the difference. */
    margin: { selling: number; cost: number; margin: number };
  };
  notSold: { items: StockItem[]; more: boolean };
  soldBelowCost: {
    sales: {
      itemId: string;
      name: string;
      unit: string;
      qty: string;
      saleTotal: number;
      costTotal: number;
      loss: number;
      createdAt: string;
    }[];
    more: boolean;
  };
  lowStock: { items: StockItem[]; more: boolean };
  /**
   * What sold in those days, one row for each counted item: sales on credit and for cash together,
   * with how much of it was for cash. The margin is of the sales whose cost is known in so'm.
   */
  sold: { items: SoldItem[]; more: boolean };
  /** The cash sales of those days that stand, every line of them, counted item or not. */
  cashSales: SaleTotals;
};

export type SoldItem = {
  itemId: string;
  name: string;
  unit: string;
  qty: string;
  revenue: number;
  cost: number;
  margin: number;
  cashQty: string;
  cashRevenue: number;
};

function itemsBlock(value: unknown): { items: StockItem[]; more: boolean } {
  const body = record(value);
  return { items: list(body["items"], stockItem), more: flag(body["more"]) };
}

function stockReport(value: unknown): StockReport {
  const body = record(value);
  const totals = record(body["totals"]);
  const margin = record(totals["margin"]);
  const below = record(body["sold_below_cost"]);
  const sold = record(body["sold"]);
  return {
    days: whole(body["days"]),
    totals: {
      items: whole(totals["items"]),
      low: whole(totals["low"]),
      cost: list(totals["cost"], (element) => {
        const row = record(element);
        return { currency: currency(row["currency"]), value: whole(row["value"]) };
      }),
      selling: whole(totals["selling"]),
      margin: { selling: whole(margin["selling"]), cost: whole(margin["cost"]), margin: whole(margin["margin"]) },
    },
    notSold: itemsBlock(body["not_sold"]),
    soldBelowCost: {
      sales: list(below["sales"], (element) => {
        const sale = record(element);
        return {
          itemId: text(sale["item_id"]),
          name: text(sale["name"]),
          unit: text(sale["unit"]),
          qty: qty(sale["qty"]),
          saleTotal: whole(sale["sale_total"]),
          costTotal: whole(sale["cost_total"]),
          loss: whole(sale["loss"]),
          createdAt: text(sale["created_at"]),
        };
      }),
      more: flag(below["more"]),
    },
    lowStock: itemsBlock(body["low_stock"]),
    sold: {
      items: list(sold["items"], (element) => {
        const row = record(element);
        return {
          itemId: text(row["item_id"]),
          name: text(row["name"]),
          unit: text(row["unit"]),
          qty: qty(row["qty"]),
          revenue: whole(row["revenue"]),
          cost: whole(row["cost"]),
          // Below zero when what sold cost more than it brought.
          margin: whole(row["margin"]),
          cashQty: qty(row["cash_qty"]),
          cashRevenue: whole(row["cash_revenue"]),
        };
      }),
      more: flag(sold["more"]),
    },
    cashSales: saleTotals(body["cash_sales"]),
  };
}

export type ItemFilter = "tracked" | "all" | "low";
export const ITEM_FILTERS: readonly ItemFilter[] = ["tracked", "low", "all"];

/** What may be changed about how an item is counted; a part that is absent stays as it is. */
export type ItemStockPatch = {
  tracked?: boolean;
  /** A quantity sets the level the item runs low at; null clears it. */
  lowStock?: string | null;
  unit?: string;
  barcodes?: readonly string[];
};

export type Page<T> = { items: T[]; nextCursor: string | null };

function paged<T>(key: string, item: (element: unknown) => T): (value: unknown) => Page<T> {
  return (value) => {
    const body = record(value);
    return { items: list(body[key], item), nextCursor: textOrNull(body["next_cursor"]) };
  };
}

const id = encodeURIComponent;

export function stockOf(api: ShopApi) {
  const stock = `${api.base}/stock`;
  const suppliers = `${api.base}/suppliers`;
  return {
    settings(signal?: AbortSignal): Promise<StockSettings> {
      return api.send({ method: "GET", path: `${stock}/settings`, signal, read: stockSettings });
    },

    updateSettings(refuseNegative: boolean, idempotencyKey: string): Promise<StockSettings> {
      return api.send({
        method: "PUT",
        path: `${stock}/settings`,
        body: { refuse_negative: refuseNegative },
        idempotencyKey,
        read: stockSettings,
      });
    },

    items(
      params: { q?: string; filter?: ItemFilter; cursor?: string | null; limit?: number },
      signal?: AbortSignal,
    ): Promise<Page<StockItem>> {
      return api.send({
        method: "GET",
        path: `${stock}/items`,
        query: { q: params.q, filter: params.filter, cursor: params.cursor, limit: params.limit?.toString() },
        signal,
        read: paged("items", stockItem),
      });
    },

    item(itemId: string, signal?: AbortSignal): Promise<StockItem> {
      return api.send({ method: "GET", path: `${stock}/items/${id(itemId)}`, signal, read: stockItem });
    },

    /** The item a barcode names. An unknown code is refused as NOT_FOUND, like an unknown item. */
    lookup(code: string, signal?: AbortSignal): Promise<StockItem> {
      return api.send({ method: "GET", path: `${stock}/lookup`, query: { code }, signal, read: stockItem });
    },

    updateItem(itemId: string, patch: ItemStockPatch, idempotencyKey: string): Promise<StockItem> {
      const body: Record<string, unknown> = {};
      if (patch.tracked !== undefined) {
        body["tracked"] = patch.tracked;
      }
      if (patch.lowStock === null) {
        body["clear_low_stock"] = true;
      } else if (patch.lowStock !== undefined) {
        body["low_stock"] = patch.lowStock;
      }
      if (patch.unit !== undefined) {
        body["unit"] = patch.unit;
      }
      if (patch.barcodes !== undefined) {
        body["barcodes"] = [...patch.barcodes];
      }
      return api.send({ method: "PATCH", path: `${stock}/items/${id(itemId)}`, body, idempotencyKey, read: stockItem });
    },

    movements(itemId: string, cursor: string | null, signal?: AbortSignal): Promise<Page<Movement>> {
      return api.send({
        method: "GET",
        path: `${stock}/items/${id(itemId)}/movements`,
        query: { cursor },
        signal,
        read: paged("movements", movement),
      });
    },

    report(days: number, signal?: AbortSignal): Promise<StockReport> {
      return api.send({ method: "GET", path: `${stock}/report`, query: { days: String(days) }, signal, read: stockReport });
    },

    documents(
      params: { kind?: string; status?: string; supplierId?: string; cursor?: string | null },
      signal?: AbortSignal,
    ): Promise<Page<DocumentSummary>> {
      return api.send({
        method: "GET",
        path: `${stock}/documents`,
        query: { kind: params.kind, status: params.status, supplier_id: params.supplierId, cursor: params.cursor },
        signal,
        read: paged("documents", documentSummary),
      });
    },

    document(documentId: string, signal?: AbortSignal): Promise<StockDocument> {
      return api.send({ method: "GET", path: `${stock}/documents/${id(documentId)}`, signal, read: stockDocument });
    },

    /** Writes a document as a draft, or with `post` writes it and makes it take effect in one step. */
    createDocument(document: NewDocument, post: boolean, idempotencyKey: string): Promise<StockDocument> {
      const body = documentBody(document);
      if (post) {
        body["post"] = true;
      }
      return api.send({ method: "POST", path: `${stock}/documents`, body, idempotencyKey, read: stockDocument });
    },

    /** Replaces what a draft says, as a whole. */
    updateDocument(documentId: string, document: NewDocument, idempotencyKey: string): Promise<StockDocument> {
      return api.send({
        method: "PUT",
        path: `${stock}/documents/${id(documentId)}`,
        body: documentBody(document),
        idempotencyKey,
        read: stockDocument,
      });
    },

    postDocument(documentId: string, idempotencyKey: string): Promise<StockDocument> {
      return api.send({
        method: "POST",
        path: `${stock}/documents/${id(documentId)}/post`,
        idempotencyKey,
        read: stockDocument,
      });
    },

    cancelDocument(documentId: string, reason: string, idempotencyKey: string): Promise<StockDocument> {
      return api.send({
        method: "POST",
        path: `${stock}/documents/${id(documentId)}/cancel`,
        body: { reason },
        idempotencyKey,
        read: stockDocument,
      });
    },

    /** Records a sale for cash. Refused as a whole, with nothing kept, when the shop refuses sales beyond stock. */
    recordSale(input: NewSale, idempotencyKey: string): Promise<RecordedSale> {
      return api.send({ method: "POST", path: `${stock}/sales`, body: saleBody(input), idempotencyKey, read: recordedSale });
    },

    /** The cash sales of a day or of a stretch of days, newest first; with no day, today's in Tashkent. */
    sales(params: SalesFilter, signal?: AbortSignal): Promise<SalesPage> {
      return api.send({
        method: "GET",
        path: `${stock}/sales`,
        query: {
          day_from: params.dayFrom,
          day_to: params.dayTo,
          item_id: params.itemId,
          // The server refuses the two together: one's own sales are asked for by `mine` alone.
          seller_id: params.mine ? undefined : params.sellerId,
          mine: params.mine ? "true" : undefined,
          status: params.status,
          cursor: params.cursor,
          limit: params.limit?.toString(),
        },
        signal,
        read: salesPage,
      });
    },

    sale(saleId: string, signal?: AbortSignal): Promise<Sale> {
      return api.send({ method: "GET", path: `${stock}/sales/${id(saleId)}`, signal, read: sale });
    },

    /** Takes a sale back, with a reason that stays in the books. A sale already taken back is refused. */
    cancelSale(saleId: string, reason: string, idempotencyKey: string): Promise<Sale> {
      return api.send({
        method: "POST",
        path: `${stock}/sales/${id(saleId)}/cancel`,
        body: { reason },
        idempotencyKey,
        read: sale,
      });
    },

    suppliers(
      params: { q?: string; status?: "active" | "archived"; cursor?: string | null; limit?: number },
      signal?: AbortSignal,
    ): Promise<SupplierList> {
      return api.send({
        method: "GET",
        path: suppliers,
        query: { q: params.q, status: params.status, cursor: params.cursor, limit: params.limit?.toString() },
        signal,
        read: (value) => {
          const body = record(value);
          return {
            suppliers: list(body["suppliers"], supplier),
            totals: list(body["totals"], (element) => {
              const row = record(element);
              return { currency: currency(row["currency"]), owed: whole(row["owed"]) };
            }),
            nextCursor: textOrNull(body["next_cursor"]),
          };
        },
      });
    },

    supplier(supplierId: string, cursor: string | null, signal?: AbortSignal): Promise<SupplierAccount> {
      return api.send({
        method: "GET",
        path: `${suppliers}/${id(supplierId)}`,
        query: { cursor },
        signal,
        read: (value) => {
          const body = record(value);
          return {
            supplier: supplier(body["supplier"]),
            entries: list(body["entries"], supplierEntry),
            nextCursor: textOrNull(body["next_cursor"]),
          };
        },
      });
    },

    createSupplier(input: SupplierInput, idempotencyKey: string): Promise<Supplier> {
      return api.send({ method: "POST", path: suppliers, body: supplierBody(input), idempotencyKey, read: supplier });
    },

    updateSupplier(supplierId: string, input: SupplierInput, idempotencyKey: string): Promise<Supplier> {
      return api.send({
        method: "PUT",
        path: `${suppliers}/${id(supplierId)}`,
        body: supplierBody(input),
        idempotencyKey,
        read: supplier,
      });
    },

    setArchived(supplierId: string, archived: boolean, idempotencyKey: string): Promise<Supplier> {
      return api.send({
        method: "POST",
        path: `${suppliers}/${id(supplierId)}/${archived ? "archive" : "unarchive"}`,
        idempotencyKey,
        read: supplier,
      });
    },

    /** A payment to the supplier, or what the shop already owed them before it kept this book. */
    addEntry(
      supplierId: string,
      entry: { kind: "payment" | "opening"; amount: number; currency: Currency; note: string | null; method?: PaymentMethod | null },
      idempotencyKey: string,
    ): Promise<Supplier> {
      const body: Record<string, unknown> = { kind: entry.kind, amount: entry.amount, currency: entry.currency };
      if (entry.method) {
        body["method"] = entry.method;
      }
      if (entry.note !== null) {
        body["note"] = entry.note;
      }
      return api.send({
        method: "POST",
        path: `${suppliers}/${id(supplierId)}/entries`,
        body,
        idempotencyKey,
        read: (value) => supplier(record(value)["supplier"]),
      });
    },

    cancelEntry(supplierId: string, entryId: string, reason: string, idempotencyKey: string): Promise<Supplier> {
      return api.send({
        method: "POST",
        path: `${suppliers}/${id(supplierId)}/entries/${id(entryId)}/cancel`,
        body: { reason },
        idempotencyKey,
        read: (value) => supplier(record(value)["supplier"]),
      });
    },
  };
}

export type StockApi = ReturnType<typeof stockOf>;
