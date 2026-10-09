import type { CalendarDay } from "../shared/format";
import { addDays, compareDays, parseIsoDate } from "../shared/promise";
import type { PaymentCard, SettingValue, SubscriptionAction, SubscriptionState } from "./adminApi";

/**
 * What the administrator's forms check before anything is sent. Ports of the server's rules, which are
 * the authority and are applied again there: backend/src/qarz/domain/platform_settings.py,
 * domain/admin_subscription.py and application/admin.py (`clean_reason`).
 */

// --- reasons and codes ---------------------------------------------------------------------------------

export const REASON_MIN = 3;
export const REASON_MAX = 500;

/** A reason with white space tidied: 3 to 500 characters, or null when it does not fit. */
export function cleanReason(raw: string): string | null {
  const reason = raw.split(/\s+/).filter(Boolean).join(" ");
  const length = [...reason].length;
  return length >= REASON_MIN && length <= REASON_MAX ? reason : null;
}

/** A code from the authenticator: exactly six digits. */
export function isCode(text: string): boolean {
  return /^[0-9]{6}$/.test(text);
}

// --- platform settings ---------------------------------------------------------------------------------

export type SettingRule =
  | { kind: "switch" }
  | { kind: "number"; low: number; high: number }
  /** Up to ten cards, each sixteen digits and a name; the first is the primary. An empty list clears them. */
  | { kind: "cards" }
  /** A Telegram group's chat identifier, a negative number, or nothing to clear it. */
  | { kind: "chat" };

export const CARD_DIGITS = 16;
export const CARDS_MAX = 10;
export const CARD_LABEL_MAX = 40;
export const MIN_CHAT_ID = -(10 ** 15);

/** Each setting's type and range. The API returns values only, so the table is repeated here. */
export const SETTING_RULES: Readonly<Record<string, SettingRule>> = {
  trial_on: { kind: "switch" },
  trial_days: { kind: "number", low: 1, high: 365 },
  price_uzs: { kind: "number", low: 1_000, high: 10_000_000 },
  payment_cards: { kind: "cards" },
  review_group: { kind: "chat" },
  sms_on: { kind: "switch" },
  sms_monthly_quota: { kind: "number", low: 0, high: 100_000 },
  online_pay_on: { kind: "switch" },
  free_plan_on: { kind: "switch" },
  free_plan_customers: { kind: "number", low: 1, high: 10_000 },
  permissions_on: { kind: "switch" },
  customer_links_on: { kind: "switch" },
};

export type Parsed = { ok: true; value: SettingValue } | { ok: false };

/**
 * The rows of the card editor from the text its field holds. The form keeps one text for each setting;
 * for the cards that text is the rows as JSON, as typed. Anything else is no rows.
 */
export function cardRows(input: string): PaymentCard[] {
  let rows: unknown;
  try {
    rows = JSON.parse(input === "" ? "[]" : input);
  } catch {
    return [];
  }
  if (!Array.isArray(rows)) {
    return [];
  }
  return rows.map((row: unknown) => {
    const held = (typeof row === "object" && row !== null ? row : {}) as Record<string, unknown>;
    return { number: typeof held["number"] === "string" ? held["number"] : "", label: typeof held["label"] === "string" ? held["label"] : "" };
  });
}

/** A row as the server stores it: the number without spaces, the name trimmed. */
function cleanCard(row: PaymentCard): PaymentCard {
  return { number: row.number.replace(/ /g, ""), label: row.label.trim() };
}

export type CardProblem = "number" | "label" | "duplicate";

/** What is wrong with each row, in the server's order of checks; null for a row that is fine. */
export function cardProblems(rows: readonly PaymentCard[]): (CardProblem | null)[] {
  const cleaned = rows.map(cleanCard);
  return cleaned.map((card, place) => {
    if (!/^[0-9]{16}$/.test(card.number)) {
      return "number";
    }
    const length = [...card.label].length;
    if (length < 1 || length > CARD_LABEL_MAX) {
      return "label";
    }
    return cleaned.slice(0, place).some((earlier) => earlier.number === card.number) ? "duplicate" : null;
  });
}

/** What was typed for a setting, as the value the server stores; a switch is never typed. */
export function parseSetting(rule: SettingRule, input: string): Parsed {
  const text = input.trim();
  switch (rule.kind) {
    case "switch":
      return text === "true" || text === "false" ? { ok: true, value: text === "true" } : { ok: false };
    case "number": {
      const digits = text.replace(/[\s\u00a0]/g, "");
      if (!/^[0-9]{1,9}$/.test(digits)) {
        return { ok: false };
      }
      const value = Number(digits);
      return value >= rule.low && value <= rule.high ? { ok: true, value } : { ok: false };
    }
    case "cards": {
      const rows = cardRows(text);
      if (rows.length > CARDS_MAX || cardProblems(rows).some((problem) => problem !== null)) {
        return { ok: false };
      }
      return { ok: true, value: rows.map(cleanCard) };
    }
    case "chat": {
      if (text === "") {
        return { ok: true, value: null };
      }
      if (!/^-[0-9]{1,16}$/.test(text)) {
        return { ok: false };
      }
      const value = Number(text);
      return value >= MIN_CHAT_ID ? { ok: true, value } : { ok: false };
    }
  }
}

/** A stored value as the text of its field; the cards as the rows of their editor (`cardRows`). */
export function settingText(value: SettingValue): string {
  if (Array.isArray(value)) {
    return JSON.stringify(value.map((card) => ({ number: card.number, label: card.label })));
  }
  return value === null ? "" : String(value);
}

/** How a card is named where its number has no place: the name and the last four digits. */
export function cardTag(card: PaymentCard): string {
  return `${card.label} ··${card.number.slice(-4)}`;
}

// --- a shop's subscription -----------------------------------------------------------------------------

export const MAX_TRIAL_DAYS_AHEAD = 365;
export const MAX_PAID_DAYS_AHEAD = 3 * 366;

/**
 * The changes that apply to a subscription as it stands. Suspension has one way out, so nothing else is
 * offered while a shop is suspended; a paying shop has no trial to extend.
 */
export function offeredActions(subscription: SubscriptionState): SubscriptionAction[] {
  if (subscription.storedState === "suspended") {
    return ["unsuspend"];
  }
  const actions: SubscriptionAction[] = [];
  if (subscription.state !== "active") {
    actions.push("trial");
  }
  if (subscription.storedState === "trial") {
    actions.push("endTrial");
  }
  actions.push("paidThrough", "suspend");
  return actions;
}

export type DayRange = { min: CalendarDay; max: CalendarDay };

/** The days a change may set: a trial ends today or within a year; a paid period from yesterday on. */
export function dateRange(action: SubscriptionAction, today: CalendarDay): DayRange | null {
  if (action === "trial") {
    return { min: today, max: addDays(today, MAX_TRIAL_DAYS_AHEAD) };
  }
  return action === "paidThrough" ? { min: addDays(today, -1), max: addDays(today, MAX_PAID_DAYS_AHEAD) } : null;
}

/** The typed day when it is one the change may set; null otherwise. */
export function dateInRange(text: string, range: DayRange): string | null {
  const day = parseIsoDate(text.trim());
  return day !== null && compareDays(day, range.min) >= 0 && compareDays(day, range.max) <= 0 ? text.trim() : null;
}

/** Why the server refused a change (`fields.reason` of SUBSCRIPTION_CHANGE_REFUSED). */
export const CHANGE_REFUSALS = [
  "already_suspended",
  "not_suspended",
  "suspended",
  "paid",
  "date_out_of_range",
  "not_in_trial",
  "not_paid",
] as const;
export type ChangeRefusal = (typeof CHANGE_REFUSALS)[number];

export function isChangeRefusal(value: unknown): value is ChangeRefusal {
  return CHANGE_REFUSALS.some((reason) => reason === value);
}

// --- the shop's owner ------------------------------------------------------------------------------------

/** A Telegram user identifier as typed: digits only, a positive whole number JavaScript holds exactly. */
export function telegramId(text: string): number | null {
  const typed = text.trim();
  if (!/^[1-9][0-9]{0,15}$/.test(typed)) {
    return null;
  }
  const id = Number(typed);
  return Number.isSafeInteger(id) ? id : null;
}

/** Why the server refused to give a shop to a person (`fields.reason` of OWNER_REASSIGNMENT_REFUSED). */
export const OWNER_REFUSALS = ["unknown_user", "already_owner"] as const;
export type OwnerRefusal = (typeof OWNER_REFUSALS)[number];

export function isOwnerRefusal(value: unknown): value is OwnerRefusal {
  return OWNER_REFUSALS.some((reason) => reason === value);
}

// --- support access --------------------------------------------------------------------------------------

/** How long a support access may last, in whole hours (backend/src/qarz/domain/support_access.py, BR-31). */
export const SUPPORT_HOURS_MIN = 1;
export const SUPPORT_HOURS_MAX = 24;

/** The typed number of hours when it is a whole number from 1 to 24; null otherwise. */
export function parseHours(input: string): number | null {
  const text = input.trim();
  if (!/^[0-9]{1,2}$/.test(text)) {
    return null;
  }
  const hours = Number(text);
  return hours >= SUPPORT_HOURS_MIN && hours <= SUPPORT_HOURS_MAX ? hours : null;
}

/**
 * Whether an access lets its administrator read the shop at `now`. The server's word is from the moment
 * it answered; an access whose time has run out since then is no longer open.
 */
export function isOpenAccess(access: { state: string; endsAt: string }, now: Date): boolean {
  const ends = new Date(access.endsAt).getTime();
  return access.state === "active" && !Number.isNaN(ends) && ends > now.getTime();
}

// --- subscription receipts -----------------------------------------------------------------------------

/** The statuses a receipt has, as the queue filters by them (backend/src/qarz/domain/subscription_receipts.py). */
export const RECEIPT_STATUSES = ["submitted", "approved", "rejected"] as const;
export const RECEIPT_MONTHS_MAX = 36;

/** The months an approval counts: a whole number from 1 to 36 in plain digits, or null. */
export function parseReceiptMonths(input: string): number | null {
  const typed = input.trim();
  if (!/^[0-9]{1,2}$/.test(typed)) {
    return null;
  }
  const months = Number(typed);
  return months >= 1 && months <= RECEIPT_MONTHS_MAX ? months : null;
}

/** An approval's note: none when nothing is written, otherwise a reason like any other; `false` when it does not fit. */
export function cleanNote(raw: string): string | null | false {
  if (raw.trim() === "") {
    return null;
  }
  return cleanReason(raw) ?? false;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(text: string): boolean {
  return UUID.test(text);
}

/** A short code for an identifier: its last six characters, enough to tell two administrators apart. */
export function shortId(id: string): string {
  return id.slice(-6);
}

/**
 * Who did something, as the panel names them: an administrator by the end of their identifier, or a
 * Telegram administrator of the review group, who has no account here, by the Telegram identifier.
 */
export function actorName(
  adminId: string | null,
  telegramId: number | null,
  groupAdmin: (telegramId: number) => string,
  nobody: string,
): string {
  if (adminId !== null) {
    return shortId(adminId);
  }
  return telegramId === null ? nobody : groupAdmin(telegramId);
}
