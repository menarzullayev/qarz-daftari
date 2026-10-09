import { describe, expect, it } from "vitest";

import { buildDocument, type DocumentDraft, draftOf, draftTotal, emptyDraft, oneMore } from "./documentDraft";
import { lineCost, readCount, readQty, readUnitCost, thousandthsOf, isNegative } from "./quantity";
import type { StockDocument } from "./stockApi";

const ITEM = { id: "44444444-4444-4444-8444-444444444441", name: "Shakar", unit: "kg" };
const OTHER = { id: "44444444-4444-4444-8444-444444444442", name: "Choy", unit: "dona" };
const SUPPLIER = "77777777-7777-4777-8777-777777777771";
const CUSTOMER = "11111111-1111-4111-8111-111111111111";

function draft(kind: DocumentDraft["kind"], patch: Partial<DocumentDraft> = {}): DocumentDraft {
  return { ...emptyDraft(kind, "2026-10-06"), ...patch };
}
const line = (key: number, qty: string, cost = "", item = ITEM) => ({ key, item, added: null, qty, cost });

describe("quantities and costs as typed", () => {
  it("reads a quantity with a comma or a full stop and sends the full stop", () => {
    expect(readQty("1,5")).toEqual({ ok: true, api: "1.5", thousandths: 1500 });
    expect(readQty("0.125")).toEqual({ ok: true, api: "0.125", thousandths: 125 });
  });

  it("refuses zero for a quantity that moves and takes it for a count", () => {
    expect(readQty("0")).toEqual({ ok: false, problem: "not_positive" });
    expect(readCount("0")).toEqual({ ok: true, api: "0", thousandths: 0 });
    expect(readCount("abc").ok).toBe(false);
    expect(readQty("1.2345")).toEqual({ ok: false, problem: "too_precise" });
  });

  it("reads a cost in so'm as whole so'm and in dollars as cents; zero is a cost", () => {
    expect(readUnitCost("12 000", "UZS")).toEqual({ ok: true, amount: 12000 });
    expect(readUnitCost("0", "UZS")).toEqual({ ok: true, amount: 0 });
    expect(readUnitCost("12.50", "USD")).toEqual({ ok: true, amount: 1250 });
    expect(readUnitCost("12.5", "UZS").ok).toBe(false);
    expect(readUnitCost("", "UZS").ok).toBe(false);
  });

  it("multiplies on whole numbers and rounds halves up once", () => {
    expect(lineCost(1500, 12000)).toBe(18000);
    expect(lineCost(125, 4)).toBe(1); // 0.125 x 4 = 0.5
    expect(lineCost(333, 1)).toBe(0); // 0.333
    expect(lineCost(100, 3)).toBe(0); // 0.3
    expect(lineCost(1100, 3)).toBe(3); // 3.3
    // 0.1 + 0.2 never happens: the product is of integers, beyond what a double holds exactly.
    expect(lineCost(999_999_999, 1_000_000_000)).toBe(999_999_999_000_000);
    expect(lineCost(-1, 5)).toBeNull();
  });

  it("reads the API's signed quantities", () => {
    expect(thousandthsOf("-2.5")).toBe(-2500);
    expect(thousandthsOf("7")).toBe(7000);
    expect(thousandthsOf("1.2345")).toBeNull();
    expect(isNegative("-0.001")).toBe(true);
    expect(isNegative("0")).toBe(false);
  });

  it("adds one to a line for a second scan of the same label", () => {
    expect(oneMore("1")).toBe("2");
    expect(oneMore("1,5")).toBe("2,5");
    expect(oneMore("0")).toBe("1");
    expect(oneMore("abc")).toBe("abc");
  });
});

describe("a receipt", () => {
  it("without a supplier sends no supplier and no paid: the server takes it as paid in full", () => {
    const built = buildDocument(draft("receipt", { lines: [line(1, "2", "12000"), line(2, "1,5", "4000", OTHER)] }));
    expect(built).toEqual({
      ok: true,
      total: 30000,
      document: {
        kind: "receipt",
        docDate: "2026-10-06",
        note: null,
        supplierId: null,
        currency: "UZS",
        lines: [
          { qty: "2", unitCost: 12000, itemId: ITEM.id },
          { qty: "1.5", unitCost: 4000, itemId: OTHER.id },
        ],
      },
    });
  });

  it("from a supplier is paid in full, on credit, or in part", () => {
    const base = { supplierId: SUPPLIER, lines: [line(1, "2", "12000")] };
    const paid = (patch: Partial<DocumentDraft>) => {
      const built = buildDocument(draft("receipt", { ...base, ...patch }));
      return built.ok ? built.document.paid : built.problems;
    };
    expect(paid({ payment: "full" })).toBe(24000);
    expect(paid({ payment: "credit" })).toBe(0);
    expect(paid({ payment: "part", paidText: "10 000" })).toBe(10000);
  });

  it("refuses a part that is more than the total, or is not an amount", () => {
    const base = { supplierId: SUPPLIER, payment: "part" as const, lines: [line(1, "2", "12000")] };
    for (const paidText of ["24001", "", "abc", "10.5"]) {
      const built = buildDocument(draft("receipt", { ...base, paidText }));
      expect(built.ok, paidText).toBe(false);
      expect(!built.ok && built.problems.paid, paidText).toBe(true);
    }
  });

  it("in dollars carries the currency and costs in cents", () => {
    const built = buildDocument(draft("receipt", { currency: "USD", lines: [line(1, "3", "1.25")] }));
    expect(built.ok && built.document.currency).toBe("USD");
    expect(built.ok && built.document.lines[0]).toEqual({ qty: "3", unitCost: 125, itemId: ITEM.id });
    expect(built.total).toBe(375);
  });

  it("may carry a good met for the first time", () => {
    const added = { name: "Yangi choy", unit: "dona", price: 15000, barcode: "4006381333931" };
    const built = buildDocument(draft("receipt", { lines: [{ key: 1, item: null, added, qty: "10", cost: "9000" }] }));
    expect(built.ok && built.document.lines).toEqual([{ qty: "10", unitCost: 9000, newItem: added }]);
  });

  it("is refused with no lines, a line without a cost, or a quantity of zero", () => {
    const empty = buildDocument(draft("receipt"));
    expect(!empty.ok && empty.problems.empty).toBe(true);
    const bad = buildDocument(draft("receipt", { lines: [line(1, "2", ""), line(2, "0", "100", OTHER), line(3, "1", "100")] }));
    expect(!bad.ok && bad.problems.lines).toEqual({ 1: { qty: false, cost: true }, 2: { qty: true, cost: false } });
  });

  it("is refused when its total is beyond what one document may hold", () => {
    const built = buildDocument(draft("receipt", { lines: [line(1, "999999", "1000000000")] }));
    expect(!built.ok && built.problems.total).toBe(true);
  });
});

describe("how the money was paid", () => {
  const paid = (patch: Partial<DocumentDraft>) => {
    const built = buildDocument(draft("receipt", { lines: [line(1, "2", "12000")], method: "card", ...patch }));
    return built.ok ? built.document.method : "refused";
  };

  it("is said when something is paid now: a receipt for cash, one paid in full, one paid in part", () => {
    expect(paid({})).toBe("card");
    expect(paid({ supplierId: SUPPLIER, payment: "full" })).toBe("card");
    expect(paid({ supplierId: SUPPLIER, payment: "part", paidText: "1000" })).toBe("card");
  });

  it("is not said for goods taken on credit, a part of nothing, or a kind that pays nothing", () => {
    expect(paid({ supplierId: SUPPLIER, payment: "credit" })).toBeUndefined();
    expect(paid({ supplierId: SUPPLIER, payment: "part", paidText: "0" })).toBeUndefined();
    const writeOff = buildDocument(draft("write_off", { reason: "lost", method: "cash", lines: [line(1, "1")] }));
    expect(writeOff.ok && "method" in writeOff.document).toBe(false);
  });

  it("is never said where nobody was asked: a shop without a cash book", () => {
    expect(paid({ method: null })).toBeUndefined();
  });

  it("is said for money handed back to a customer, and not when all of a return lowers the debt", () => {
    const base = { customerId: CUSTOMER, method: "cash" as const, lines: [line(1, "1", "15000")] };
    const some = buildDocument(draft("customer_return", { ...base, paidText: "5000" }));
    expect(some.ok && some.document.method).toBe("cash");
    const none = buildDocument(draft("customer_return", base));
    expect(none.ok && "method" in none.document).toBe(false);
  });
});

describe("the other kinds", () => {
  it("a return to a supplier needs the supplier", () => {
    const built = buildDocument(draft("supplier_return", { lines: [line(1, "1", "100")] }));
    expect(!built.ok && built.problems.supplier).toBe(true);
    const ok = buildDocument(draft("supplier_return", { supplierId: SUPPLIER, lines: [line(1, "1", "100")] }));
    expect(ok.ok && ok.document).toMatchObject({ kind: "supplier_return", supplierId: SUPPLIER, currency: "UZS" });
    expect(ok.ok && "paid" in ok.document).toBe(false);
  });

  it("a write-off needs its reason and carries no price, no currency and no payment", () => {
    const none = buildDocument(draft("write_off", { lines: [line(1, "1")] }));
    expect(!none.ok && none.problems.reason).toBe(true);
    const built = buildDocument(draft("write_off", { reason: "expired", note: "  muddati   o'tgan ", lines: [line(1, "1", "999")] }));
    expect(built).toEqual({
      ok: true,
      total: 0,
      document: { kind: "write_off", docDate: "2026-10-06", note: "muddati o'tgan", reason: "expired", lines: [{ qty: "1", itemId: ITEM.id }] },
    });
  });

  it("a stocktake takes a count of zero and no price", () => {
    const built = buildDocument(draft("stocktake", { lines: [line(1, "0"), line(2, "12,5", "", OTHER)] }));
    expect(built.ok && built.document.lines).toEqual([
      { qty: "0", itemId: ITEM.id },
      { qty: "12.5", itemId: OTHER.id },
    ]);
  });

  it("a customer's return needs the customer, is in so'm, and says how much is handed back in money", () => {
    const none = buildDocument(draft("customer_return", { lines: [line(1, "1", "15000")] }));
    expect(!none.ok && none.problems.customer).toBe(true);
    const all = buildDocument(draft("customer_return", { customerId: CUSTOMER, currency: "USD", lines: [line(1, "2", "15000")] }));
    expect(all.ok && all.document).toMatchObject({ customerId: CUSTOMER, paid: 0 });
    expect(all.ok && "currency" in all.document).toBe(false);
    const some = buildDocument(draft("customer_return", { customerId: CUSTOMER, paidText: "10000", lines: [line(1, "2", "15000")] }));
    expect(some.ok && some.document.paid).toBe(10000);
    const over = buildDocument(draft("customer_return", { customerId: CUSTOMER, paidText: "30001", lines: [line(1, "2", "15000")] }));
    expect(!over.ok && over.problems.paid).toBe(true);
  });

  it("refuses a date that is not one and a note that is too long", () => {
    const built = buildDocument(draft("stocktake", { docDate: "", note: "a".repeat(201), lines: [line(1, "1")] }));
    expect(!built.ok && [built.problems.date, built.problems.note]).toEqual([true, true]);
  });
});

describe("a stored draft back in the form", () => {
  const stored: StockDocument = {
    id: "88888888-8888-4888-8888-888888888881",
    kind: "receipt",
    number: 7,
    status: "draft",
    docDate: "2026-10-05",
    supplier: { id: SUPPLIER, name: "Baraka ulgurji" },
    customer: null,
    reason: null,
    note: "ertalabki",
    createdBy: "33333333-3333-4333-8333-333333333333",
    createdAt: "2026-10-05T05:00:00+00:00",
    postedAt: null,
    cancelledAt: null,
    cancelReason: null,
    money: { currency: "UZS", total: 30000, paid: 10000 },
    lines: [{ lineNo: 1, item: ITEM, qty: "2.5", unitCost: 12000, lineTotal: 30000 }],
  };

  it("keeps what was typed: the lines, the supplier, the part that was paid", () => {
    const form = draftOf(stored);
    expect(form).toMatchObject({ kind: "receipt", supplierId: SUPPLIER, payment: "part", note: "ertalabki" });
    expect(form?.lines).toEqual([{ key: 1, item: ITEM, added: null, qty: "2,5", cost: "12 000" }]);
    expect(form && draftTotal(form)).toBe(30000);
    const again = form && buildDocument(form);
    expect(again?.ok && again.document.paid).toBe(10000);
  });

  it("is not offered for editing when its prices were kept from this member", () => {
    const hidden: StockDocument = { ...stored, lines: [{ lineNo: 1, item: ITEM, qty: "2.5" }] };
    delete hidden.money;
    expect(draftOf(hidden)).toBeNull();
  });
});
