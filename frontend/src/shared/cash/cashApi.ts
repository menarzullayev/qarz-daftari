import { reading, type ShopApi } from "../api";
import { CURRENCIES, type Currency } from "../money";

/**
 * The cash book of a shop (expansion module H): income and expense by cash, card or transfer, under the
 * shop's own categories. Built on the shop API's `send` in the module that is loaded with the cash
 * screen, so these calls are not part of the first load. Shapes follow
 * backend/src/qarz/interface/cash_answers.py.
 *
 * Every amount is a whole number of its currency's minor unit (so'm, or cents), and every figure is of
 * one currency: nothing here adds two. All of it is behind a platform switch; while that is off every
 * call is answered 404, and the section is not offered at all.
 */

const { record, text, textOrNull, whole, flag, list } = reading;

export const DIRECTIONS = ["income", "expense"] as const;
export type Direction = (typeof DIRECTIONS)[number];

export const METHODS = ["cash", "card", "transfer"] as const;
export type Method = (typeof METHODS)[number];

/** The currencies of the service (`shared/money.ts`): so'm, and dollars in a shop that works in them. */
export const CASH_CURRENCIES = CURRENCIES;
export type CashCurrency = Currency;

export type CashCategory = {
  id: string;
  direction: Direction;
  name: string;
  archived: boolean;
  /** The category customers' payments land in: renamed freely, never written to by hand, archived or deleted. */
  fixed: boolean;
};

export type CashCategories = { items: CashCategory[]; currencies: CashCurrency[] };

export type CashEntry = {
  id: string;
  direction: Direction;
  method: Method;
  currency: CashCurrency;
  amount: number;
  category: { id: string; name: string };
  note: string | null;
  day: string;
  createdAt: string;
  authorId: string;
  /** "ledger": a customer's payment, written by the ledger and cancelled only by reversing it there. */
  source: "manual" | "ledger";
  customer: { id: string; displayName: string | null } | null;
  /** `reason` is null when the ledger cancelled the entry: the payment was reversed. */
  cancelled: { at: string; by: string | null; reason: string | null } | null;
};

/** One method of one currency: `opening + income - expense = closing`. */
export type CashLine = {
  currency: CashCurrency;
  method: Method;
  opening: number;
  income: number;
  expense: number;
  closing: number;
  count: number;
};

/** One currency over all of its methods. */
export type CashTotal = Omit<CashLine, "method">;

export type CashDay = {
  date: string;
  balances: CashLine[];
  totals: CashTotal[];
  entries: CashEntry[];
  nextCursor: string | null;
};

export type CashSummary = {
  from: string;
  to: string;
  balances: CashLine[];
  totals: CashTotal[];
  categories: { category: CashCategory; currency: CashCurrency; amount: number; count: number }[];
  /** Only the days something stands on, oldest first. */
  days: { date: string; currency: CashCurrency; income: number; expense: number }[];
};

export type NewCashEntry = {
  direction: Direction;
  method: Method;
  currency: CashCurrency;
  amount: number;
  categoryId: string;
  note: string | null;
  /** "YYYY-MM-DD"; null for today. */
  day: string | null;
};

class Unreadable extends RangeError {
  constructor(what: string) {
    super(`not a cash book answer: ${what}`);
  }
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], what: string): T {
  const found = allowed.find((candidate) => candidate === value);
  if (found === undefined) {
    throw new Unreadable(what);
  }
  return found;
}

function category(value: unknown): CashCategory {
  const body = record(value);
  return {
    id: text(body["id"]),
    direction: oneOf(body["direction"], DIRECTIONS, "direction"),
    name: text(body["name"]),
    archived: flag(body["archived"]),
    fixed: flag(body["fixed"]),
  };
}

function categories(value: unknown): CashCategories {
  const body = record(value);
  return {
    items: list(body["items"], category),
    currencies: list(body["currencies"], (code) => oneOf(code, CASH_CURRENCIES, "currency")),
  };
}

function entry(value: unknown): CashEntry {
  const body = record(value);
  const of = record(body["category"]);
  const customer = body["customer"] === null ? null : record(body["customer"]);
  const cancelled = body["cancelled"] === null ? null : record(body["cancelled"]);
  return {
    id: text(body["id"]),
    direction: oneOf(body["direction"], DIRECTIONS, "direction"),
    method: oneOf(body["method"], METHODS, "method"),
    currency: oneOf(body["currency"], CASH_CURRENCIES, "currency"),
    amount: whole(body["amount"]),
    category: { id: text(of["id"]), name: text(of["name"]) },
    note: textOrNull(body["note"]),
    day: text(body["day"]),
    createdAt: text(body["created_at"]),
    authorId: text(body["author_id"]),
    source: oneOf(body["source"], ["manual", "ledger"] as const, "source"),
    customer: customer === null ? null : { id: text(customer["id"]), displayName: textOrNull(customer["display_name"]) },
    cancelled:
      cancelled === null
        ? null
        : { at: text(cancelled["at"]), by: textOrNull(cancelled["by"]), reason: textOrNull(cancelled["reason"]) },
  };
}

function total(value: unknown): CashTotal {
  const body = record(value);
  const figures = {
    currency: oneOf(body["currency"], CASH_CURRENCIES, "currency"),
    // A balance may be below zero: the book records what was written into it.
    opening: whole(body["opening"]),
    income: whole(body["income"]),
    expense: whole(body["expense"]),
    closing: whole(body["closing"]),
    count: whole(body["count"]),
  };
  // The server states that this holds; an answer in which it does not is not shown as a balance.
  if (figures.opening + figures.income - figures.expense !== figures.closing) {
    throw new Unreadable("a balance that does not add up");
  }
  return figures;
}

function line(value: unknown): CashLine {
  return { ...total(value), method: oneOf(record(value)["method"], METHODS, "method") };
}

function day(value: unknown): CashDay {
  const body = record(value);
  return {
    date: text(body["date"]),
    balances: list(body["balances"], line),
    totals: list(body["totals"], total),
    entries: list(body["entries"], entry),
    nextCursor: textOrNull(body["next_cursor"]),
  };
}

function summary(value: unknown): CashSummary {
  const body = record(value);
  return {
    from: text(body["from"]),
    to: text(body["to"]),
    balances: list(body["balances"], line),
    totals: list(body["totals"], total),
    categories: list(body["categories"], (element) => {
      const row = record(element);
      return {
        category: category(row["category"]),
        currency: oneOf(row["currency"], CASH_CURRENCIES, "currency"),
        amount: whole(row["amount"]),
        count: whole(row["count"]),
      };
    }),
    days: list(body["days"], (element) => {
      const row = record(element);
      return {
        date: text(row["date"]),
        currency: oneOf(row["currency"], CASH_CURRENCIES, "currency"),
        income: whole(row["income"]),
        expense: whole(row["expense"]),
      };
    }),
  };
}

const written = (value: unknown): CashEntry => entry(record(value)["entry"]);

export function cashOf(api: ShopApi) {
  const base = `${api.base}/cash`;
  const categoryPath = (id: string) => `${base}/categories/${encodeURIComponent(id)}`;
  return {
    /** One Tashkent day ("YYYY-MM-DD"; null for today) and a page of its entries, newest first. */
    day(date: string | null, cursor: string | null, signal?: AbortSignal): Promise<CashDay> {
      return api.send({ method: "GET", path: `${base}/day`, query: { date, cursor }, signal, read: day });
    },

    summary(from: string, to: string, signal?: AbortSignal): Promise<CashSummary> {
      return api.send({ method: "GET", path: `${base}/summary`, query: { from, to }, signal, read: summary });
    },

    categories(signal?: AbortSignal): Promise<CashCategories> {
      return api.send({ method: "GET", path: `${base}/categories`, signal, read: categories });
    },

    record(input: NewCashEntry, idempotencyKey: string): Promise<CashEntry> {
      if (!Number.isSafeInteger(input.amount) || input.amount <= 0) {
        throw new RangeError("amount must be a whole positive number of the currency's minor unit");
      }
      const body: Record<string, unknown> = {
        direction: input.direction,
        method: input.method,
        amount: input.amount,
        category_id: input.categoryId,
      };
      if (input.currency !== "UZS") {
        body["currency"] = input.currency;
      }
      if (input.note !== null) {
        body["note"] = input.note;
      }
      if (input.day !== null) {
        body["day"] = input.day;
      }
      return api.send({ method: "POST", path: `${base}/entries`, body, idempotencyKey, read: written });
    },

    cancel(entryId: string, reason: string, idempotencyKey: string): Promise<CashEntry> {
      return api.send({
        method: "POST",
        path: `${base}/entries/${encodeURIComponent(entryId)}/cancellation`,
        body: { reason },
        idempotencyKey,
        read: written,
      });
    },

    createCategory(direction: Direction, name: string, idempotencyKey: string): Promise<CashCategory> {
      return api.send({
        method: "POST",
        path: `${base}/categories`,
        body: { direction, name },
        idempotencyKey,
        read: category,
      });
    },

    /** Renames a category, archives it or brings it back; what is left out stays as it is. */
    updateCategory(
      id: string,
      change: { name?: string; archived?: boolean },
      idempotencyKey: string,
    ): Promise<CashCategory> {
      return api.send({ method: "PATCH", path: categoryPath(id), body: change, idempotencyKey, read: category });
    },

    deleteCategory(id: string, idempotencyKey: string): Promise<void> {
      return api.send({ method: "DELETE", path: categoryPath(id), idempotencyKey, read: () => undefined });
    },

    /** Copies the ledger's past payments into the book; `since` ("YYYY-MM-DD") leaves out older ones. */
    backfill(since: string | null, idempotencyKey: string): Promise<number> {
      return api.send({
        method: "POST",
        path: `${base}/backfill`,
        body: { since },
        idempotencyKey,
        read: (value) => whole(record(value)["written"]),
      });
    },
  };
}

export type CashApi = ReturnType<typeof cashOf>;
