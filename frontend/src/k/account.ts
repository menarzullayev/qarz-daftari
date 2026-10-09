/**
 * Reading the account behind a customer's read-only link (backend: application/customer_shares.py,
 * `CustomerShareService.view`).
 *
 * The secret is the part of the address after "#". A browser never sends that part to a server, so the
 * secret is in no access log, no proxy and no referrer; this page sends it once, in a header, to the one
 * route that reads it. Nothing here writes it anywhere.
 */

export const ACCOUNT_PATH = "/api/v1/customer-share";
export const TOKEN_HEADER = "X-Share-Token";

// 32 random bytes in the URL-safe alphabet (backend/src/qarz/domain/customer_share.py).
const TOKEN_SHAPE = /^[A-Za-z0-9_-]{43}$/;

/** The secret from the address's fragment ("#..."), or null when what is there is not one. */
export function readToken(hash: string): string | null {
  const text = hash.startsWith("#") ? hash.slice(1) : hash;
  return TOKEN_SHAPE.test(text) ? text : null;
}

export type Line = { name: string; qty: string; unit: string; unitPrice: number; lineTotal: number };

export type Entry = {
  kind: string;
  amount: number;
  createdAt: string;
  promisedDate: string | null;
  reversed: boolean;
  lines: Line[];
  /** "USD" on an entry in dollars, whose amount is then whole cents. Absent: whole UZS. */
  currency?: "USD";
};

/**
 * What is owed in US dollars, in whole cents, in a shop that keeps dollar debts beside so'm ones. The
 * two are separate debts: nothing on the page is a sum of them.
 */
export type Dollars = { balance: number; overdue: number; dueToday: number };

export type Account = {
  shopName: string;
  shopPhone: string | null;
  firstName: string;
  lang: string;
  /** Whole UZS the customer owes; negative when they paid more than they owed. */
  balance: number;
  overdue: number;
  dueToday: number;
  /** Absent when the shop does not work in dollars: the page then says nothing about them. */
  usd?: Dollars;
  expiresAt: string;
  entries: Entry[];
  entriesTotal: number;
};

/**
 * What the page can come to:
 *  - "gone": no such link. Unknown, ended, expired and switched off are one answer, on purpose;
 *  - "limited": too many requests from this address, the proxy's refusal;
 *  - "offline": the request did not arrive, or the answer was not the account.
 */
export type Outcome = { status: "ok"; account: Account } | { status: "gone" | "limited" | "offline" };

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("not an object");
  }
  return value as Record<string, unknown>;
}

function text(value: unknown): string {
  if (typeof value !== "string") {
    throw new TypeError("not a text");
  }
  return value;
}

function textOrNull(value: unknown): string | null {
  return value === null ? null : text(value);
}

function whole(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value)) {
    throw new TypeError("not a whole number");
  }
  return value;
}

function list(value: unknown): unknown[] {
  if (!Array.isArray(value)) {
    throw new TypeError("not a list");
  }
  return value;
}

function flag(value: unknown): boolean {
  if (typeof value !== "boolean") {
    throw new TypeError("not a flag");
  }
  return value;
}

function line(value: unknown): Line {
  const body = record(value);
  return {
    name: text(body["name"]),
    qty: text(body["qty"]),
    unit: text(body["unit"]),
    unitPrice: whole(body["unit_price"]),
    lineTotal: whole(body["line_total"]),
  };
}

/** So'm is the absence of the key; "USD" is dollars; any other word is not the account. */
function currency(value: unknown): { currency?: "USD" } {
  if (value === undefined) {
    return {};
  }
  if (value !== "USD") {
    throw new TypeError("not a currency");
  }
  return { currency: "USD" };
}

/** The dollar figures, read as strictly as the so'm ones; absent, there are none. */
function dollars(value: unknown): { usd?: Dollars } {
  if (value === undefined) {
    return {};
  }
  const body = record(value);
  const overdue = record(body["overdue"]);
  return { usd: { balance: whole(body["balance"]), overdue: whole(overdue["amount"]), dueToday: whole(overdue["due_today"]) } };
}

function entry(value: unknown): Entry {
  const body = record(value);
  return {
    ...currency(body["currency"]),
    kind: text(body["kind"]),
    amount: whole(body["amount"]),
    createdAt: text(body["created_at"]),
    promisedDate: textOrNull(body["promised_date"]),
    reversed: flag(body["reversed"]),
    lines: list(body["lines"]).map(line),
  };
}

/** The account as the API sends it, or an error: the page never shows a balance that is not a number. */
export function readAccount(value: unknown): Account {
  const body = record(value);
  const overdue = record(body["overdue"]);
  return {
    shopName: text(body["shop_name"]),
    shopPhone: textOrNull(body["shop_phone"]),
    firstName: text(body["first_name"]),
    lang: text(body["lang"]),
    balance: whole(body["balance"]),
    overdue: whole(overdue["amount"]),
    dueToday: whole(overdue["due_today"]),
    ...dollars(body["usd"]),
    expiresAt: text(body["expires_at"]),
    entries: list(body["entries"]).map(entry),
    entriesTotal: whole(body["entries_total"]),
  };
}

export type Fetch = (input: string, init: RequestInit) => Promise<Response>;

/** Asks for the account once. Never throws: every failure is one of the outcomes. */
export async function loadAccount(fetch: Fetch, token: string): Promise<Outcome> {
  let response: Response;
  try {
    response = await fetch(ACCOUNT_PATH, {
      method: "GET",
      headers: { Accept: "application/json", [TOKEN_HEADER]: token },
      // No cookie is sent and nothing is kept: the staff's session, if this browser has one, has no part
      // in this page, and the answer is read from the server every time.
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
    });
  } catch {
    return { status: "offline" };
  }
  if (response.status === 404) {
    return { status: "gone" };
  }
  if (response.status === 429) {
    return { status: "limited" };
  }
  if (!response.ok) {
    return { status: "offline" };
  }
  try {
    return { status: "ok", account: readAccount(await response.json()) };
  } catch {
    return { status: "offline" };
  }
}
