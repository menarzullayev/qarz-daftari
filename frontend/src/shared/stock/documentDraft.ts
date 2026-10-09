import { type Currency, parseMoney, amountInput } from "../money";
import { parseIsoDate } from "../promise";
import { lineCost, MAX_DOCUMENT_LINES, MAX_DOCUMENT_TOTAL, MAX_NOTE, readCount, readQty, readUnitCost, tidy } from "./quantity";
import type { NewDocument, NewDocumentLine, PaymentMethod, StockDocument, StockDocumentKind } from "./stockApi";

/**
 * A stock document while a person fills it in, and the check that turns it into a request. The rules
 * are the server's (backend/src/qarz/application/stock_documents.py, `clean_document`), repeated here
 * so that a mistake is shown beside its field before anything is sent; the server checks again.
 */

/** The kinds whose lines carry a price: what was paid for the goods, or what is given back for them. */
export const PRICED_KINDS: ReadonlySet<StockDocumentKind> = new Set(["receipt", "customer_return", "supplier_return"]);
/** The kinds that are with a supplier. A receipt may have none: goods bought for cash. */
export const SUPPLIER_KINDS: ReadonlySet<StockDocumentKind> = new Set(["receipt", "supplier_return"]);

export type NewItem = { name: string; unit: string; price: number; barcode: string | null };

export type DraftLine = {
  key: number;
  /** An item of the catalog, or null for a good met for the first time (`added`). */
  item: { id: string; name: string; unit: string } | null;
  added: NewItem | null;
  qty: string;
  cost: string;
};

/** How a receipt from a supplier is paid: all of it now, nothing now (on credit), or a part. */
export type Payment = "full" | "credit" | "part";

export type DocumentDraft = {
  kind: StockDocumentKind;
  docDate: string;
  supplierId: string | null;
  customerId: string | null;
  currency: Currency;
  payment: Payment;
  paidText: string;
  /** How what is paid now is paid; null where the shop keeps no cash book and nobody is asked. */
  method: PaymentMethod | null;
  reason: string | null;
  note: string;
  lines: DraftLine[];
};

export type DraftProblems = {
  /** By line key: which of its fields does not read. */
  lines: Record<number, { qty: boolean; cost: boolean }>;
  empty: boolean;
  tooMany: boolean;
  total: boolean;
  date: boolean;
  supplier: boolean;
  customer: boolean;
  reason: boolean;
  paid: boolean;
  note: boolean;
};

export type Built = { ok: true; document: NewDocument; total: number } | { ok: false; problems: DraftProblems; total: number };

/** The currency a document's prices are in: a customer's return is in so'm like the sale it undoes. */
export function currencyOfDraft(draft: Pick<DocumentDraft, "kind" | "currency">): Currency {
  return SUPPLIER_KINDS.has(draft.kind) ? draft.currency : "UZS";
}

/** What the lines that read so far come to; a line that does not read counts as nothing. */
export function draftTotal(draft: DocumentDraft): number {
  if (!PRICED_KINDS.has(draft.kind)) {
    return 0;
  }
  const currency = currencyOfDraft(draft);
  let total = 0;
  for (const line of draft.lines) {
    const qty = readQty(line.qty);
    const cost = readUnitCost(line.cost, currency);
    if (qty.ok && cost.ok) {
      total += lineCost(qty.thousandths, cost.amount) ?? 0;
    }
  }
  return total;
}

export function buildDocument(draft: DocumentDraft): Built {
  const { kind } = draft;
  const priced = PRICED_KINDS.has(kind);
  const currency = currencyOfDraft(draft);
  const problems: DraftProblems = {
    lines: {},
    empty: draft.lines.length === 0,
    tooMany: draft.lines.length > MAX_DOCUMENT_LINES,
    total: false,
    date: parseIsoDate(draft.docDate) === null,
    supplier: kind === "supplier_return" && draft.supplierId === null,
    customer: kind === "customer_return" && draft.customerId === null,
    reason: kind === "write_off" && draft.reason === null,
    paid: false,
    note: false,
  };
  const lines: NewDocumentLine[] = [];
  let total = 0;
  for (const line of draft.lines) {
    // A stocktake says what was counted, and nothing counted is an answer; every other line moves goods.
    const qty = kind === "stocktake" ? readCount(line.qty) : readQty(line.qty);
    const cost = priced ? readUnitCost(line.cost, currency) : null;
    if (!qty.ok || (cost !== null && !cost.ok)) {
      problems.lines[line.key] = { qty: !qty.ok, cost: cost !== null && !cost.ok };
      continue;
    }
    const built: { qty: string; unitCost?: number } = { qty: qty.api };
    if (cost !== null && cost.ok) {
      built.unitCost = cost.amount;
      total += lineCost(qty.thousandths, cost.amount) ?? MAX_DOCUMENT_TOTAL + 1;
    }
    if (line.item !== null) {
      lines.push({ ...built, itemId: line.item.id });
    } else if (line.added !== null) {
      lines.push({ ...built, newItem: line.added });
    }
  }
  problems.total = total > MAX_DOCUMENT_TOTAL;

  const note = tidy(draft.note);
  problems.note = note !== null && [...note].length > MAX_NOTE;

  const document: NewDocument = { kind, docDate: draft.docDate, note, lines };
  if (SUPPLIER_KINDS.has(kind)) {
    document.supplierId = draft.supplierId;
    document.currency = currency;
  }
  if (kind === "customer_return") {
    document.customerId = draft.customerId;
  }
  if (kind === "write_off") {
    document.reason = draft.reason;
  }
  if (kind === "receipt" && draft.supplierId !== null) {
    // Without a supplier there is nobody to owe: the server takes such a receipt as paid in full.
    if (draft.payment === "full") {
      document.paid = total;
    } else if (draft.payment === "credit") {
      document.paid = 0;
    } else {
      const paid = parseMoney(draft.paidText, currency, { min: 0, max: Math.max(total, 0) });
      problems.paid = !paid.ok;
      document.paid = paid.ok ? paid.amount : 0;
    }
  }
  if (kind === "customer_return") {
    // What is handed back in money; the rest of the goods' price lowers what the customer owes.
    if (draft.paidText.trim() === "") {
      document.paid = 0;
    } else {
      const paid = parseMoney(draft.paidText, "UZS", { min: 0, max: Math.max(total, 0) });
      problems.paid = !paid.ok;
      document.paid = paid.ok ? paid.amount : 0;
    }
  }

  // Said only when money changes hands now: goods taken on credit were paid by no method.
  if (draft.method !== null && (document.paid ?? (kind === "receipt" ? total : 0)) > 0) {
    document.method = draft.method;
  }

  const { lines: lineProblems, ...rest } = problems;
  const refused = Object.keys(lineProblems).length > 0 || Object.values(rest).some(Boolean);
  return refused ? { ok: false, problems, total } : { ok: true, document, total };
}

/** A new, empty document of a kind, dated today. */
export function emptyDraft(kind: StockDocumentKind, today: string): DocumentDraft {
  return {
    kind,
    docDate: today,
    supplierId: null,
    customerId: null,
    currency: "UZS",
    payment: "full",
    paidText: "",
    method: null,
    reason: null,
    note: "",
    lines: [],
  };
}

/**
 * A stored draft as the form holds it. Null when its prices were kept from this member: a priced
 * document read without its money cannot be saved again without losing what its author typed.
 */
export function draftOf(document: StockDocument): DocumentDraft | null {
  const priced = PRICED_KINDS.has(document.kind);
  if (priced && document.money === undefined) {
    return null;
  }
  const currency = document.money?.currency ?? "UZS";
  const total = document.money?.total ?? 0;
  const paid = document.money?.paid ?? 0;
  return {
    kind: document.kind,
    docDate: document.docDate,
    supplierId: document.supplier?.id ?? null,
    customerId: document.customer?.id ?? null,
    currency,
    payment: paid === total ? "full" : paid === 0 ? "credit" : "part",
    paidText: paid === 0 ? "" : amountInput(paid, currency),
    method: null,
    reason: document.reason,
    note: document.note ?? "",
    lines: document.lines.map((line, index) => ({
      key: index + 1,
      item: line.item,
      added: null,
      qty: line.qty.replace(".", ","),
      cost: line.unitCost === undefined ? "" : amountInput(line.unitCost, currency),
    })),
  };
}

/** One more of an item that is already a line: what a second scan of the same label means. */
export function oneMore(qtyText: string): string {
  const read = readCount(qtyText);
  if (!read.ok) {
    return qtyText;
  }
  const next = read.thousandths + 1000;
  const whole = Math.floor(next / 1000);
  const rest = next % 1000;
  return rest === 0 ? String(whole) : `${whole},${String(rest).padStart(3, "0").replace(/0+$/, "")}`;
}

/** Whether a draft line's quantity reads as a number, for the running total of a line. */
export function lineTotalOf(line: DraftLine, currency: Currency): number | null {
  const qty = readQty(line.qty);
  const cost = readUnitCost(line.cost, currency);
  return qty.ok && cost.ok ? lineCost(qty.thousandths, cost.amount) : null;
}
