import type { ApiError } from "../api";

/**
 * A shop whose trial or paid period has ended is limited: new credit sales are refused and everything
 * else still works. A suspended shop is closed to everyone but its owner, who may still look.
 */
export type ShopMode = "limited" | "suspended";

/** How many days before a period ends its owner starts to be told on the overview (REQ-057). */
export const WARN_DAYS = 7;

/**
 * What a refusal says about the shop. Staff other than the owner may not read the subscription, so
 * these two refusals are how they learn of the mode.
 */
export function modeOfRefusal(error: ApiError): ShopMode | null {
  if (error.code === "SUBSCRIPTION_LIMITED") {
    return "limited";
  }
  return error.code === "SHOP_SUSPENDED" ? "suspended" : null;
}

/** The mode a subscription state stands for, or null for a shop that works in full: one in a trial or
 * paid period, and one the free plan holds ("free"). */
export function modeOfState(state: string): ShopMode | null {
  return state === "limited" || state === "suspended" ? state : null;
}

/** Whether a period that ends in `daysLeft` days is close enough to its end to tell the owner. */
export function endsSoon(daysLeft: number | null): boolean {
  return daysLeft !== null && daysLeft <= WARN_DAYS;
}
