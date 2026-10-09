import type { ApiError, CreditSettings, LimitFigures } from "../api";
import { type AmountResult, parseWholeUzs } from "../money";
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

/** A limit as a person types it: a whole amount of UZS within the bounds the server accepts. */
export function parseLimit(input: string, bounds: CreditSettings["bounds"]): AmountResult {
  return parseWholeUzs(input, bounds.min, bounds.max);
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
