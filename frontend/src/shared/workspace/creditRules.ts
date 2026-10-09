import type { ApiError, CreditSettings, Customer, LimitFigures } from "../api";
import { type AmountResult, type Currency, parseMoney } from "../money";
import { may, type Viewer } from "../permissions";

/*
 * Credit limits as the interface needs them (REQ-044). The rules are the server's, which is the
 * authority: backend/src/qarz/domain/credit.py (`effective_limit`, `check_limit`). Keep the two in step.
 */

/** The customer's own limit if one is set, else the shop's default, else none. */
export function effectiveLimit(own: number | null, shopDefault: number | null): number | null {
  return own ?? shopDefault;
}

/**
 * Whether a credit sale of `sale` UZS would take a balance of `balance` above `limit`. A balance equal
 * to the limit is within it.
 */
export function exceedsLimit(limit: number | null, balance: number, sale: number): boolean {
  return limit !== null && balance + sale > limit;
}

/** Whether the server lets this member record a sale above the limit (it warns) or refuses it. */
export function mayExceed(viewer: Viewer, settings: Pick<CreditSettings, "sellersMayExceed">): boolean {
  return may(viewer, "entries.over_limit") || settings.sellersMayExceed;
}

/**
 * A limit as a person types it, within the bounds the server accepts: a whole amount of UZS, or for the
 * dollar limit dollars with at most two decimals, read into cents.
 */
export function parseLimit(input: string, bounds: CreditSettings["bounds"], currency: Currency = "UZS"): AmountResult {
  return parseMoney(input, currency, bounds);
}

/**
 * The limit that applies to a customer in one currency, with the balance it is compared with, or null
 * when the shop has no such currency. Each currency has its own limit and its own debt (BR-8).
 */
export function limitIn(
  currency: Currency,
  customer: Pick<Customer, "balance" | "creditLimit" | "usd">,
  settings: Pick<CreditSettings, "defaultLimit" | "usd"> | null,
): { own: number | null; limit: number | null; balance: number } | null {
  if (currency === "UZS") {
    return {
      own: customer.creditLimit,
      limit: effectiveLimit(customer.creditLimit, settings?.defaultLimit ?? null),
      balance: customer.balance,
    };
  }
  if (customer.usd === undefined) {
    return null;
  }
  return {
    own: customer.usd.creditLimit,
    limit: effectiveLimit(customer.usd.creditLimit, settings?.usd?.defaultLimit ?? null),
    balance: customer.usd.balance,
  };
}

const DIGITS = /^\d{1,15}$/;

/** The limit and the balance a LIMIT_REACHED refusal carries in `fields`, or null when it has none. */
export function refusedLimit(error: ApiError | null): LimitFigures | null {
  if (error?.code !== "LIMIT_REACHED") {
    return null;
  }
  const limit = error.fields["limit"];
  const balance = error.fields["balance"];
  if (limit === undefined || balance === undefined || !DIGITS.test(limit) || !DIGITS.test(balance)) {
    return null;
  }
  return { limit: Number(limit), balance: Number(balance) };
}
