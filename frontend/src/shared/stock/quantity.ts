import { MAX_QTY_THOUSANDTHS, parseQty, QTY_SCALE, type QtyProblem, qtyToApi } from "../goods";
import { type AmountResult, type Currency, parseMoney } from "../money";

/**
 * Quantities and costs of the stock as a person types them (backend/src/qarz/domain/stock.py is the
 * authority). A quantity has at most three decimals and is held as a whole number of thousandths; a
 * cost is a whole number of the currency's minor unit. Nothing here is a fraction: a line's total is
 * computed on whole numbers and rounded half up once, as the server does.
 */

/** The largest cost of one unit, in minor units of the document's currency. Zero is a cost: goods given free. */
export const MAX_UNIT_COST = 1_000_000_000;
export const MAX_DOCUMENT_TOTAL = 1_000_000_000_000;
export const MAX_DOCUMENT_LINES = 200;
export const MAX_NOTE = 200;
export const MAX_REASON = 200;

export type QtyText = { ok: true; api: string; thousandths: number } | { ok: false; problem: QtyProblem };

/** A quantity that moves: above zero. "1,5" and "1.5" are both read; the API gets "1.5". */
export function readQty(input: string): QtyText {
  const read = parseQty(input);
  if (!read.ok) {
    return read;
  }
  if (read.thousandths > MAX_QTY_THOUSANDTHS) {
    return { ok: false, problem: "too_large" };
  }
  return { ok: true, api: qtyToApi(read.thousandths), thousandths: read.thousandths };
}

/** What a stocktake counted, or a low-stock level: like a quantity, and zero is an answer too. */
export function readCount(input: string): QtyText {
  const read = readQty(input);
  if (!read.ok && read.problem === "not_positive") {
    return { ok: true, api: "0", thousandths: 0 };
  }
  return read;
}

/** The cost of one unit as typed, in the minor unit of `currency`: so'm are whole, dollars have cents. */
export function readUnitCost(input: string, currency: Currency): AmountResult {
  return parseMoney(input, currency, { min: 0, max: MAX_UNIT_COST });
}

/**
 * Quantity times unit cost in whole minor units, halves rounded up. The product can pass the safe
 * integers, so it is computed on big integers; null when the result itself would not be safe.
 */
export function lineCost(thousandths: number, unitCost: number): number | null {
  if (!Number.isSafeInteger(thousandths) || !Number.isSafeInteger(unitCost) || thousandths < 0 || unitCost < 0) {
    return null;
  }
  const scale = BigInt(QTY_SCALE);
  const total = (BigInt(thousandths) * BigInt(unitCost) + scale / 2n) / scale;
  return total <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(total) : null;
}

const API_QTY = /^(-?)(\d{1,12})(?:\.(\d{1,3}))?$/;

/** The API's decimal string as whole thousandths, signed; null for anything else. */
export function thousandthsOf(apiQty: string): number | null {
  const match = API_QTY.exec(apiQty);
  if (!match) {
    return null;
  }
  const size = Number(match[2]) * QTY_SCALE + Number((match[3] ?? "").padEnd(3, "0"));
  return Number.isSafeInteger(size) ? (match[1] === "-" ? -size : size) : null;
}

/** Whether a quantity of the API is below zero: the stock was sold before its receipt was written. */
export function isNegative(apiQty: string): boolean {
  return (thousandthsOf(apiQty) ?? 0) < 0;
}

/** A short text, trimmed and with single spaces; null when nothing is left. */
export function tidy(raw: string): string | null {
  const clean = raw.split(/\s+/u).filter(Boolean).join(" ");
  return clean === "" ? null : clean;
}
