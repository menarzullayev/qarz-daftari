import { type CalendarDay, tashkentDay } from "./format";
import { type AmountResult, MAX_AMOUNT, MIN_AMOUNT, parseWholeUzs } from "./money";
import { addDays, compareDays } from "./promise";

/**
 * Arithmetic for goods lines (BR-7, REQ-N06). A port of backend/src/qarz/domain/rounding.py, which is
 * the authority: the server computes every line total again.
 *
 * A quantity has at most three decimals, so it is held as a whole number of thousandths and every
 * calculation below is on integers. Binary floating point is never used for a value that is shown or
 * sent: 0.1 + 0.2 kilograms must be 0.3, and 0.125 × 4 must round to 1, not to 0.
 */

export const QTY_SCALE = 1000;
const QTY_DECIMALS = 3;
/** 999 999.999: with a price of at most 100 000 000 the product stays far inside the safe integers. */
export const MAX_QTY_THOUSANDTHS = 999_999_999;
const MAX_QTY_WHOLE_DIGITS = 6;

/** Bounds of a unit price (backend/src/qarz/domain/catalog.py). */
export const MIN_PRICE = 1;
export const MAX_PRICE = 100_000_000;

/** Bounds of the sum of an entry's lines: the same as an entry amount. */
export const MIN_LINES_SUM = MIN_AMOUNT;
export const MAX_LINES_SUM = MAX_AMOUNT;
export const MAX_LINES = 50;

export type QtyProblem = "empty" | "invalid" | "too_precise" | "not_positive" | "too_large";
export type QtyResult = { ok: true; thousandths: number } | { ok: false; problem: QtyProblem };

const QTY_TEXT = /^(\d+)(?:[.,](\d+))?$/;

/**
 * Reads a quantity as a seller types it: "2", "1.5", "1,5", "0,125". More than three decimals are
 * refused, never rounded: the seller must see the quantity that is saved.
 */
export function parseQty(input: string): QtyResult {
  const text = input.trim();
  if (text === "") {
    return { ok: false, problem: "empty" };
  }
  const match = QTY_TEXT.exec(text);
  if (!match) {
    return { ok: false, problem: "invalid" };
  }
  const whole = (match[1] ?? "").replace(/^0+(?=\d)/, "");
  const fraction = match[2] ?? "";
  if (fraction.length > QTY_DECIMALS) {
    return { ok: false, problem: "too_precise" };
  }
  if (whole.length > MAX_QTY_WHOLE_DIGITS) {
    return { ok: false, problem: "too_large" };
  }
  const thousandths = Number(whole) * QTY_SCALE + Number(fraction.padEnd(QTY_DECIMALS, "0"));
  if (thousandths === 0) {
    return { ok: false, problem: "not_positive" };
  }
  return { ok: true, thousandths };
}

const SERVER_QTY = /^(\d{1,9})(?:\.(\d{1,3})0*)?$/;

/** Reads a quantity as the API writes it ("2", "1.5", "1.500"); null for anything else. */
export function readServerQty(text: string): number | null {
  const match = SERVER_QTY.exec(text);
  if (!match) {
    return null;
  }
  const thousandths = Number(match[1]) * QTY_SCALE + Number((match[2] ?? "").padEnd(QTY_DECIMALS, "0"));
  return thousandths > 0 && Number.isSafeInteger(thousandths) ? thousandths : null;
}

function assertQty(thousandths: number): void {
  if (!Number.isSafeInteger(thousandths) || thousandths <= 0) {
    throw new RangeError("quantity must be a positive whole number of thousandths");
  }
}

/** The decimal string the API takes: "2", "1.5", "0.125". Always a full stop, never trailing zeros. */
export function qtyToApi(thousandths: number): string {
  assertQty(thousandths);
  const fraction = thousandths % QTY_SCALE;
  const whole = (thousandths - fraction) / QTY_SCALE;
  return fraction === 0 ? String(whole) : `${whole}.${String(fraction).padStart(QTY_DECIMALS, "0").replace(/0+$/, "")}`;
}

/** The quantity as people here write it, with a decimal comma: "1,5". */
export function formatQty(thousandths: number): string {
  return qtyToApi(thousandths).replace(".", ",");
}

/**
 * Quantity times unit price in whole UZS, halves rounded up (0.125 × 4 = 0.5 → 1). Null when the server
 * would refuse the line: a quantity or price out of range, or a total below 1 UZS.
 */
export function lineTotal(thousandths: number, unitPrice: number): number | null {
  if (
    !Number.isSafeInteger(thousandths) ||
    !Number.isSafeInteger(unitPrice) ||
    thousandths <= 0 ||
    thousandths > MAX_QTY_THOUSANDTHS ||
    unitPrice < MIN_PRICE ||
    unitPrice > MAX_PRICE
  ) {
    return null;
  }
  const fraction = thousandths % QTY_SCALE;
  const whole = (thousandths - fraction) / QTY_SCALE;
  // Half of a thousandth-UZS step is 500; adding it and dropping the remainder rounds halves up.
  const scaled = fraction * unitPrice + QTY_SCALE / 2;
  const total = whole * unitPrice + (scaled - (scaled % QTY_SCALE)) / QTY_SCALE;
  return total >= 1 ? total : null;
}

/** A unit price as typed: the amount grammar ("4000", "4 ming"), from 1 UZS. */
export function parsePrice(input: string): AmountResult {
  return parseWholeUzs(input, MIN_PRICE, MAX_PRICE);
}

export type SumProblem = "too_small" | "too_large";

export function linesSumProblem(sum: number): SumProblem | null {
  if (sum < MIN_LINES_SUM) {
    return "too_small";
  }
  return sum > MAX_LINES_SUM ? "too_large" : null;
}

/** The last Tashkent calendar day on which goods can still be added to a sale: the day after it. */
export function lastDayForGoods(saleDay: CalendarDay): CalendarDay {
  return addDays(saleDay, 1);
}

/**
 * Whether goods can still be added to a sale made at `createdAt` (REQ-038): until the end of the day
 * after the sale, by the Tashkent calendar. The server decides; this only hides an action it would refuse.
 */
export function goodsWindowOpen(createdAt: Date, now: Date): boolean {
  if (Number.isNaN(createdAt.getTime())) {
    return false;
  }
  return compareDays(tashkentDay(now), lastDayForGoods(tashkentDay(createdAt))) <= 0;
}
