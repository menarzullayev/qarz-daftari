/**
 * Money is whole UZS (REQ-N06). The server is the authority on amounts; the client only parses what a
 * person typed and formats what the server returned.
 */

const MIN_AMOUNT = 100;
const MAX_AMOUNT = 100_000_000;

/** Formats whole UZS with thin spaces between thousands, for example 45 000. */
export function formatUzs(amount: number): string {
  if (!Number.isSafeInteger(amount)) {
    throw new RangeError("amount must be a whole number of UZS");
  }
  const sign = amount < 0 ? "-" : "";
  const digits = Math.abs(amount).toString();
  return sign + digits.replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
}

/**
 * Parses an amount as a seller types it: "45000", "45 000", "45.000", "45k", "45 ming", "45 тыс".
 * Returns null for anything that is not a whole amount within the accepted range; decimals are rejected,
 * never rounded.
 */
export function parseUzs(input: string): number | null {
  const text = input.trim().toLowerCase();
  const match = /^(\d{1,3}(?:[ .\u00a0]\d{3})+|\d+)\s*(k|ming|тыс)?$/u.exec(text);
  if (!match) {
    return null;
  }
  const base = Number((match[1] ?? "").replace(/[ .\u00a0]/g, ""));
  const amount = match[2] ? base * 1000 : base;
  if (!Number.isSafeInteger(amount) || amount < MIN_AMOUNT || amount > MAX_AMOUNT) {
    return null;
  }
  return amount;
}
