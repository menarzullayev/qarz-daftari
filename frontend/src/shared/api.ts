import type { components } from "./api.generated";
import { lineTotal, MAX_LINES, qtyToApi, readServerQty } from "./goods";
import type { Currency } from "./money";
import { isRole, type Role } from "./navigation";

/**
 * Client for the API (/api/v1): the staff workspace and a customer's own accounts. The server is the
 * authority: it checks the role, the amounts and the dates on every call. This module only shapes
 * requests and refuses a response that does not look like the contract, so a screen never shows a
 * balance that is not a whole number of UZS.
 */

export type Fetch = (input: string, init: RequestInit) => Promise<Response>;

/**
 * The API's own shapes, as sent (snake_case), generated from the back end's description: the request
 * bodies, and the answers the description names field by field. The screens use the types below; the
 * readers and the request builders in this module are checked against these, so a field the back end
 * renames, removes or retypes fails the type check here once the types are regenerated.
 */
export type Wire = components["schemas"];

/**
 * The Mini App holds a bearer token in memory. The web panel relies on the HTTP-only session cookie and
 * must add the CSRF token it received at sign-in to every request that changes something.
 */
export type ApiAuth = { kind: "bearer"; token: string } | { kind: "cookie"; csrfToken: string | null };

/** `code` values that do not come from the server. */
export const NETWORK_ERROR = "NETWORK";
export const BAD_RESPONSE = "BAD_RESPONSE";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  /** Text from the server, already in the user's language; null when the server said nothing usable. */
  readonly serverMessage: string | null;
  readonly fields: Readonly<Record<string, string>>;
  /** Whole seconds to wait before trying again, when the server said (`Retry-After`); otherwise null. */
  readonly retryAfter: number | null;

  constructor(
    status: number,
    code: string,
    serverMessage: string | null,
    fields: Readonly<Record<string, string>> = {},
    retryAfter: number | null = null,
  ) {
    super(code);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.serverMessage = serverMessage;
    this.fields = fields;
    this.retryAfter = retryAfter;
  }
}

export function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

/** Any failure as an ApiError, so a screen has one thing to show. */
export function toApiError(error: unknown): ApiError {
  return error instanceof ApiError ? error : new ApiError(0, BAD_RESPONSE, null);
}

export type Customer = {
  id: string;
  displayName: string;
  phone: string | null;
  status: string;
  remindersOff: boolean;
  /** The customer's own credit limit in whole UZS; null when the shop's default applies (REQ-044). */
  creditLimit: number | null;
  /** Whole UZS the customer owes. */
  balance: number;
  /** The same in US dollars, beside the so'm and never added to them. Absent: the shop has no dollars. */
  usd?: CustomerDollars;
};

/**
 * What a customer owes in dollars, in whole cents. `creditLimit` is the customer's own dollar limit
 * (null: the shop's default applies). `overdue` comes with a debtor and with a customer's page,
 * `paymentHistory` with the page only.
 */
export type CustomerDollars = {
  balance: number;
  creditLimit: number | null;
  overdue?: Overdue;
  paymentHistory?: PaymentHistory | null;
};

/** "USD" beside an amount that is whole cents; absent, the amount is whole so'm (see `currencyOf`). */
export type Tagged = { currency?: Currency };

export type Overdue = { amount: number; since: string | null; days: number; dueToday: number };
export type Debtor = Customer & { overdue: Overdue };

export type PaymentHistory = {
  onTimePercent: number;
  onTimeAmount: number;
  dueAmount: number;
  longestDelayDays: number;
};

/** One good on a credit sale. The name, unit and price are the line's own and never change (INV-17). */
export type GoodsLine = {
  lineNo: number;
  catalogItemId: string | null;
  name: string;
  /** Quantity in thousandths: 1.5 kg is 1500. */
  qty: number;
  unit: string;
  unitPrice: number;
  lineTotal: number;
};

/**
 * A line to save: a catalog item (its name and unit come from the catalog, the price is this line's), or
 * a good typed by name. `qty` is in thousandths.
 */
export type NewLine =
  | { catalogItemId: string; qty: number; unitPrice: number }
  | { name: string; unit: string | null; qty: number; unitPrice: number };

export type Entry = {
  id: string;
  seq: number;
  kind: string;
  amount: number;
  note: string | null;
  createdAt: string;
  promisedDate: string | null;
  reversesId: string | null;
  reversed: boolean;
  disputed: boolean;
  /** Membership of the staff member who recorded it; null when the server does not say. */
  authorId: string | null;
  /** Empty for an amount-only entry. */
  lines: GoodsLine[];
  /** Every promised date the entry has carried, oldest first; the last one is the current date. */
  promises: PromiseRecord[];
  /** The newest request to move the entry's date, whatever its state; null when there was none. */
  dateRequest: DateRequest | null;
} & Tagged;

/**
 * One promised date of an entry (INV-9). `actor` says where it came from: "default" (the shop's usual
 * term), "staff" (chosen or changed by the shop), "customer_request" (asked by the customer, accepted).
 */
export type PromiseRecord = { promisedDate: string; actor: string; reason: string | null; createdAt: string };

/**
 * A customer's request to move the promised date of one entry (REQ-066). `status`: open, accepted,
 * declined, or expired (the entry was paid or reversed, or the shop set another date itself).
 */
export type DateRequest = {
  id: string;
  entryId: string;
  status: string;
  requestedDate: string;
  reason: string | null;
  declineReason: string | null;
  createdAt: string;
  closedAt: string | null;
};

/** An open request as a manager or an owner sees it. `promisedDate` is the date it would replace. */
export type OpenDateRequest = DateRequest & {
  customerId: string;
  customerName: string;
  amount: number;
  promisedDate: string | null;
} & Tagged;

/** What the server answers to a changed promised date: both dates, and a request the change closed. */
export type ChangedPromise = {
  entryId: string;
  promisedDate: string;
  previousDate: string;
  /** The customer's request that this date satisfied and closed as accepted; null when none was closed. */
  dateRequest: DateRequest | null;
};

export type CustomerDetail = Customer & {
  overdue: Overdue;
  paymentHistory: PaymentHistory | null;
  entries: Entry[];
  entriesTotal: number;
  /** The customer's payment notices that wait for the shop's decision, oldest first. */
  paymentNotices: PaymentNotice[];
};

/**
 * A customer's word that they paid an amount (REQ-060). It changes nothing by itself: staff accept it,
 * which records a payment, or decline it. `status`: sent, accepted, declined, or expired (nobody
 * decided within fourteen days).
 */
export type PaymentNotice = {
  id: string;
  status: string;
  /** Whole UZS the customer stated. */
  amount: number;
  /** Whole UZS recorded when it was accepted; it may differ from what was stated. Null until then. */
  recordedAmount: number | null;
  hasReceipt: boolean;
  declineReason: string | null;
  createdAt: string;
  closedAt: string | null;
  expiresAt: string;
  /** For staff only: the same receipt file was sent to this shop before. */
  receiptSeenBefore: boolean;
} & Tagged;

/** An open notice as staff see it in the shop's list; the balance is in the notice's currency. */
export type OpenPaymentNotice = PaymentNotice & { customerId: string; customerName: string; customerBalance: number };

/** Where a receipt can be opened for a few minutes. The address is a credential: it is never stored. */
export type ReceiptLink = { url: string; expiresAt: string };

/** What a receipt may be (backend/src/qarz/domain/files.py). */
export const RECEIPT_MAX_BYTES = 5 * 1024 * 1024;
export const RECEIPT_TYPES: readonly string[] = ["image/jpeg", "image/png", "image/webp", "application/pdf"];

export type OverviewFigures = {
  outstanding: number;
  debtors: number;
  overdueAmount: number;
  overdueCustomers: number;
  dueToday: number;
};
/** `usd`: the same figures of the dollar debts, in cents; absent when the shop has no dollars. */
export type Overview = OverviewFigures & { usd?: OverviewFigures };

export type Page<T> = { items: T[]; nextCursor: string | null };

export type EntryKind = "credit" | "payment";
/**
 * With `lines` the amount is not sent: the server uses the sum of the lines (REQ-037). With
 * `currency: "USD"` the amount is whole cents, and there are no lines: goods are priced in so'm.
 */
/** How a payment was made; it decides which balance of the cash book the money goes to. */
export const PAYMENT_METHODS = ["cash", "card", "transfer"] as const;
export type PaymentMethod = (typeof PAYMENT_METHODS)[number];

/** `method` is for a payment, and only while the cash book is on: otherwise the server does not know the field. */
export type NewEntry = {
  kind: EntryKind;
  note: string | null;
  promisedDate: string | null;
  method?: PaymentMethod;
} & (
  | { amount: number; currency?: Currency; lines?: never }
  | { lines: readonly NewLine[]; amount?: never; currency?: never }
);
export type RecordedEntry = {
  entry: { id: string; kind: string; amount: number; promisedDate: string | null; lines: GoodsLine[] } & Tagged;
  /** The customer after the entry; `balance` is the new balance. */
  customer: Customer;
  /** Set when the sale was saved although it took the balance above the limit that applies (REQ-044). */
  limitWarning: LimitFigures | null;
} & StockNoted;

/**
 * What a sale with goods did to the stock that the seller should know (the expansion's module I): an
 * item went below zero, or was sold in another unit than it is counted in and so was not taken out.
 * Quantities are the API's decimal strings. Absent unless the server sent some, which it only does
 * while the stock is switched on.
 */
export type StockWarning =
  | { kind: "negative"; itemId: string; name: string; onHand: string }
  | { kind: "unit"; itemId: string; name: string; unit: string };
export type StockNoted = { stockWarnings?: StockWarning[] };

/** A credit limit and the balance that met it, both in the entry's currency. */
export type LimitFigures = { limit: number; balance: number };

/** `creditLimit`: a number sets the customer's own limit, null removes it, absent leaves it. */
export type CustomerPatch = {
  displayName?: string;
  phone?: string | null;
  remindersOff?: boolean;
  creditLimit?: number | null;
  /** The dollar limit in cents, the same way; only in a shop that works in dollars. */
  creditLimitUsd?: number | null;
};

/**
 * The shop's rules for selling on credit (REQ-044). `bounds` are what the server accepts as a limit.
 * `usd` is the default dollar limit with its own bounds, in cents: a separate limit for a separate debt.
 */
export type CreditSettings = {
  defaultLimit: number | null;
  sellersMayExceed: boolean;
  bounds: { min: number; max: number };
  usd?: { defaultLimit: number | null; bounds: { min: number; max: number } };
};
export type CreditSettingsPatch = {
  defaultLimit?: number | null;
  sellersMayExceed?: boolean;
  defaultLimitUsd?: number | null;
};

/** One fixed wording of a reminder, in every language the server has it: language code to text. */
export type ReminderTemplate = {
  id: number;
  dueToday: Readonly<Record<string, string>>;
  overdue: Readonly<Record<string, string>>;
};
export type ReminderSettings = {
  on: boolean;
  /** The Tashkent hour at which automatic reminders go out. */
  hour: number;
  template: number;
  smsOn: boolean;
  /** The first and the last hour a shop may choose. */
  hours: { first: number; last: number };
  templates: ReminderTemplate[];
};
export type ReminderSettingsPatch = { on?: boolean; hour?: number; template?: number; smsOn?: boolean };

/** A reminder that was sent by hand: through which channel ("telegram" or "sms") and for what amount. */
export type SentReminder = { channel: string; amount: number; usdAmount?: number };

/** A customer with something due and no channel to be reminded through (REQ-043). */
export type UnreachableCustomer = {
  customerId: string;
  displayName: string;
  phone: string | null;
  amount: number;
  /** What is due in dollars, in cents; absent when the shop has no dollars. */
  usdAmount?: number;
};

/**
 * The free plan as it stands for the shop, sent only while the platform has it switched on: how many
 * customers it holds and how many the shop has, what follows the running period if it is not paid
 * ("free" or "limited"; null when none runs), and SMS: whether the platform offers it, whether this
 * shop has it (a paid period alone does), and how many of the month's quota are left.
 */
export type SubscriptionPlan = {
  freeCustomers: number;
  customers: number;
  afterPeriod: string | null;
  sms: { offered: boolean; included: boolean; quota: number; left: number };
};

/**
 * The shop's subscription as its owner sees it. `state`: trial, active, limited or suspended, and free
 * while the free plan holds a shop that has no period.
 */
export type Subscription = {
  state: string;
  /** Null from a server whose free plan is switched off. */
  plan: SubscriptionPlan | null;
  /** The last day of the trial or paid period, as an ISO date; null when no period is running. */
  endsOn: string | null;
  daysLeft: number | null;
  priceUzs: number;
  /** The primary card's number; null until the administrator has set a card. */
  cardNumber: string | null;
  /** The cards the payment may be transferred to, in the order they are offered: the first is the primary. */
  cards: PaymentCard[];
};

/** A card to pay the subscription to: sixteen digits and the name the administrator gave it. */
export type PaymentCard = { number: string; label: string };

/** A card number in groups of four, as it is printed on the card. */
export function groupedCard(number: string): string {
  return number.replace(/(.{4})(?=.)/g, "$1 ");
}

/** How a receipt names the card it was paid to: the label and the last four digits, never the number. */
export function cardTag(card: PaymentCard): string {
  return `${card.label} ··${card.number.slice(-4)}`;
}

export type AddedLines = { id: string; amount: number; lines: GoodsLine[] } & StockNoted;
export type ChosenPromise = { id: string; amount: number; promisedDate: string };

export type CatalogItem = {
  id: string;
  name: string;
  unit: string;
  /** Current price in whole UZS; a saved goods line keeps the price it was sold at. */
  price: number;
  /** Added by a seller typing a good on a sale and not yet reviewed by a manager. */
  learned: boolean;
  status: "active" | "hidden";
  mergedInto: string | null;
};
export type NewCatalogItem = { name: string; unit: string | null; price: number };
export type CatalogItemPatch = { name?: string; unit?: string; price?: number };
export type CatalogAction = "hide" | "unhide" | "accept" | "dismiss";

/**
 * `usdOn` is there only while the platform offers dollars: whether this shop also works in them.
 * Absent, dollars do not exist for this shop and nothing about them is shown.
 */
export type ShopSettings = { id: string; name: string; lang: string; defaultPromiseDays: number; usdOn?: boolean };
export type ShopSettingsPatch = { name?: string; lang?: string; defaultPromiseDays?: number; usdOn?: boolean };

export type ShopMembership = {
  shopId: string;
  name: string;
  role: Role;
  /** The signed-in person's membership in this shop; null when the server does not say. */
  membershipId: string | null;
};
/**
 * `permissionsOn`: the server keeps a set of permissions per member, and each shop answers what the
 * signed-in member may do there (`myPermissions`). Off, the role alone says it.
 */
export type MyShops = {
  items: ShopMembership[];
  activeShop: string | null;
  permissionsOn: boolean;
  /** The platform has switched the cash book on: a shop then answers its cash routes, and offers it. */
  cashBookOn: boolean;
  /** The platform has switched the stock on: a shop then answers its stock and supplier routes. */
  stockOn: boolean;
};

/** A customer's objection to one entry. `status`: open, declined, withdrawn, or reversed (the shop agreed). */
export type Dispute = { id: string; status: string; reason: string; declineReason: string | null };

/** An open dispute as a manager or an owner sees it. `amount` is the disputed entry's. */
export type OpenDispute = Dispute & {
  entryId: string;
  createdAt: string;
  customerId: string;
  customerName: string;
  amount: number;
} & Tagged;

/** Whether a customer is connected to a Telegram account. `status` is the server's word for the link. */
export type LinkState = { linked: boolean; status: string | null; since: string | null };

/**
 * What follows "/start " in the bot's deep link. It is a credential shown once: it lives in component
 * state only and is never written to the address, to storage or to a log. Null when the server answers
 * a repeated request, whose stored answer no longer carries it.
 */
export type IssuedLink = { start: string | null; expiresAt: string | null };

export type CounterCode = { exists: boolean; since: string | null };
export type WaitingPerson = { id: string; name: string; since: string };

/** One of the signed-in person's own customer accounts. */
export type MyAccount = {
  linkId: string;
  shopName: string;
  displayName: string;
  balance: number;
  /** What is owed in dollars, in cents; absent when the shop has no dollars. */
  usd?: { balance: number };
};

/** An entry as the customer sees it: no note and no author, which are the shop's own (REQ-045). */
export type AccountEntry = {
  id: string;
  kind: string;
  amount: number;
  createdAt: string;
  promisedDate: string | null;
  reversesId: string | null;
  reversed: boolean;
  disputed: boolean;
  dispute: Dispute | null;
  lines: GoodsLine[];
  promises: PromiseRecord[];
  dateRequest: DateRequest | null;
} & Tagged;

/** The dollar book of a customer's own account, in cents. */
export type AccountDollars = {
  balance: number;
  overdueAmount: number;
  dueToday: number;
  paymentHistory: PaymentHistory | null;
};

export type AccountDetail = Omit<MyAccount, "usd"> & {
  usd?: AccountDollars;
  overdueAmount: number;
  dueToday: number;
  removalRequested: boolean;
  /** The customer's own payment history indicator (BR-9); null while nothing has fallen due. */
  paymentHistory: PaymentHistory | null;
  entries: AccountEntry[];
  entriesTotal: number;
  /** The customer's own recent payment notices with their outcome, newest first. */
  paymentNotices: PaymentNotice[];
};

/** `removed`: the data is gone now. Otherwise it waits until `waitingForBalance` UZS are paid. */
export type RemovalOutcome = { removed: boolean; waitingForBalance: number | null; waitingForUsd?: number };

/** The text of a dispute or decline reason: 3 to 300 characters once white space is tidied. */
export const REASON_MIN = 3;
export const REASON_MAX = 300;
export function cleanReason(raw: string): string | null {
  const reason = raw.split(/\s+/).filter(Boolean).join(" ");
  return reason.length >= REASON_MIN && reason.length <= REASON_MAX ? reason : null;
}

// --- reading responses -------------------------------------------------------------------------------

class Malformed extends Error {}

type Json = Record<string, unknown>;

function record(value: unknown): Json {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Malformed();
  }
  return value as Json;
}

function text(value: unknown): string {
  if (typeof value !== "string") {
    throw new Malformed();
  }
  return value;
}

function textOrNull(value: unknown): string | null {
  return value === null || value === undefined ? null : text(value);
}

/** A whole number. Amounts are whole UZS end to end; a fraction means the response is not the contract. */
function whole(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value)) {
    throw new Malformed();
  }
  return value;
}

function flag(value: unknown): boolean {
  if (typeof value !== "boolean") {
    throw new Malformed();
  }
  return value;
}

function list<T>(value: unknown, item: (element: unknown) => T): T[] {
  if (!Array.isArray(value)) {
    throw new Malformed();
  }
  return value.map(item);
}

/**
 * Reads one answer field by field against `T`, its shape in the API's description. The field must be one
 * the back end declares, and `get` also needs a reader that takes everything the back end may send for
 * it: `text` for a field that may be null, or for a number, does not compile. What arrives is still
 * checked as it is read; the description is not taken on trust.
 */
function fieldsOf<T>(value: unknown) {
  const body = record(value);
  return {
    get<K extends keyof T & string, R extends (value: unknown) => unknown>(
      key: K,
      // `never` when the reader does not take everything the API sends for this field.
      read: R & (T[K] extends ReturnType<R> ? unknown : never),
    ): ReturnType<R> {
      return read(body[key]) as ReturnType<R>;
    },
    /** A field with a reader of its own (a nested shape, a list, a closed set of words): only its name is checked. */
    raw<K extends keyof T & string>(key: K): unknown {
      return body[key];
    },
  };
}

/** The currency beside an amount: nothing for so'm, "USD" for dollars; any other word is not the contract. */
function tagged(value: unknown): Tagged {
  if (value === undefined || value === null || value === "UZS") {
    return {};
  }
  if (value !== "USD") {
    throw new Malformed();
  }
  return { currency: "USD" };
}

/** `{ usd: ... }` when the answer carries dollar figures, and nothing when it does not. */
function dollars<T>(value: unknown, read: (value: unknown) => T): { usd?: T } {
  return value === undefined || value === null ? {} : { usd: read(value) };
}

function customerDollars(value: unknown): CustomerDollars {
  const body = fieldsOf<Wire["CustomerDollars"]>(value);
  const late = body.raw("overdue");
  const history = body.raw("payment_history");
  return {
    balance: body.get("balance", whole),
    creditLimit: body.get("credit_limit", wholeOrNull),
    ...(late === undefined || late === null ? {} : { overdue: overdue(late) }),
    ...(history === undefined ? {} : { paymentHistory: paymentHistory(history) }),
  };
}

function customer(value: unknown): Customer {
  const body = fieldsOf<Wire["Customer"]>(value);
  return {
    ...dollars(body.raw("usd"), customerDollars),
    id: body.get("id", text),
    displayName: body.get("display_name", text),
    phone: body.get("phone", textOrNull),
    status: body.get("status", text),
    remindersOff: body.get("reminders_off", flag),
    creditLimit: body.get("credit_limit", wholeOrNull),
    balance: body.get("balance", whole),
  };
}

function overdue(value: unknown): Overdue {
  const body = fieldsOf<Wire["Overdue"]>(value);
  return {
    amount: body.get("amount", whole),
    since: body.get("since", textOrNull),
    days: body.get("days", whole),
    dueToday: body.get("due_today", whole),
  };
}

function debtor(value: unknown): Debtor {
  return { ...customer(value), overdue: overdue(fieldsOf<Wire["Debtor"]>(value).raw("overdue")) };
}

function goodsLine(value: unknown): GoodsLine {
  const body = fieldsOf<Wire["EntryLine"]>(value);
  const qty = readServerQty(body.get("qty", text));
  if (qty === null) {
    throw new Malformed();
  }
  return {
    lineNo: body.get("line_no", whole),
    catalogItemId: body.get("catalog_item_id", textOrNull),
    name: body.get("name", text),
    qty,
    unit: body.get("unit", text),
    unitPrice: body.get("unit_price", whole),
    lineTotal: body.get("line_total", whole),
  };
}

/** A server that does not send goods lines yet answers without the field: that is an entry with none. */
function goodsLines(value: unknown): GoodsLine[] {
  return value === undefined || value === null ? [] : list(value, goodsLine);
}

function promiseRecord(value: unknown): PromiseRecord {
  const body = fieldsOf<Wire["Promise"]>(value);
  return {
    promisedDate: body.get("promised_date", text),
    actor: body.get("actor", text),
    reason: body.get("reason", textOrNull),
    createdAt: body.get("created_at", text),
  };
}

/** A server that does not send the history yet answers without the field: that is an entry with none. */
function promiseRecords(value: unknown): PromiseRecord[] {
  return value === undefined || value === null ? [] : list(value, promiseRecord);
}

function dateRequest(value: unknown): DateRequest {
  const body = fieldsOf<Wire["DateRequest"]>(value);
  return {
    id: body.get("id", text),
    entryId: body.get("entry_id", text),
    status: body.get("status", text),
    requestedDate: body.get("requested_date", text),
    reason: body.get("reason", textOrNull),
    declineReason: body.get("decline_reason", textOrNull),
    createdAt: body.get("created_at", text),
    closedAt: body.get("closed_at", textOrNull),
  };
}

function dateRequestOrNull(value: unknown): DateRequest | null {
  return value === undefined || value === null ? null : dateRequest(value);
}

function openDateRequest(value: unknown): OpenDateRequest {
  const body = record(value);
  return {
    ...dateRequest(body),
    customerId: text(body["customer_id"]),
    customerName: text(body["customer_name"]),
    amount: whole(body["amount"]),
    promisedDate: textOrNull(body["promised_date"]),
    ...tagged(body["currency"]),
  };
}

function changedPromise(value: unknown): ChangedPromise {
  const body = record(value);
  const changed = record(body["entry"]);
  return {
    entryId: text(changed["id"]),
    promisedDate: text(changed["promised_date"]),
    previousDate: text(changed["previous_date"]),
    dateRequest: dateRequestOrNull(body["date_request"]),
  };
}

function entry(value: unknown): Entry {
  const body = fieldsOf<Wire["Entry"]>(value);
  return {
    id: body.get("id", text),
    seq: body.get("seq", whole),
    kind: body.get("kind", text),
    amount: body.get("amount", whole),
    note: body.get("note", textOrNull),
    createdAt: body.get("created_at", text),
    promisedDate: body.get("promised_date", textOrNull),
    reversesId: body.get("reverses_id", textOrNull),
    reversed: body.get("reversed", flag),
    disputed: body.get("disputed", flag),
    authorId: body.get("author_id", textOrNull),
    lines: goodsLines(body.raw("lines")),
    promises: promiseRecords(body.raw("promises")),
    dateRequest: dateRequestOrNull(body.raw("date_request")),
    ...tagged(body.raw("currency")),
  };
}

function paymentHistory(value: unknown): PaymentHistory | null {
  if (value === null || value === undefined) {
    return null;
  }
  const body = fieldsOf<Wire["PaymentHistory"]>(value);
  return {
    onTimePercent: body.get("on_time_percent", whole),
    onTimeAmount: body.get("on_time_amount", whole),
    dueAmount: body.get("due_amount", whole),
    longestDelayDays: body.get("longest_delay_days", whole),
  };
}

function customerDetail(value: unknown): CustomerDetail {
  const body = fieldsOf<Wire["CustomerDetail"]>(value);
  return {
    ...customer(value),
    overdue: overdue(body.raw("overdue")),
    paymentHistory: paymentHistory(body.raw("payment_history")),
    entries: list(body.raw("entries"), entry),
    entriesTotal: body.get("entries_total", whole),
    paymentNotices: paymentNotices(body.raw("payment_notices")),
  };
}

/** Checked against the notice as staff are sent it; a customer's own has no `receipt_seen_before`. */
function paymentNotice(value: unknown): PaymentNotice {
  const body = fieldsOf<Wire["StaffPaymentNotice"]>(value);
  return {
    id: body.get("id", text),
    status: body.get("status", text),
    amount: body.get("amount", whole),
    recordedAmount: body.get("recorded_amount", wholeOrNull),
    hasReceipt: body.get("has_receipt", flag),
    declineReason: body.get("decline_reason", textOrNull),
    createdAt: body.get("created_at", text),
    closedAt: body.get("closed_at", textOrNull),
    expiresAt: body.get("expires_at", text),
    receiptSeenBefore: body.raw("receipt_seen_before") === true,
    ...tagged(body.raw("currency")),
  };
}

/** A server that does not send notices yet answers without the field: that is an account with none. */
function paymentNotices(value: unknown): PaymentNotice[] {
  return value === undefined || value === null ? [] : list(value, paymentNotice);
}

function openPaymentNotice(value: unknown): OpenPaymentNotice {
  const body = record(value);
  return {
    ...paymentNotice(body),
    customerId: text(body["customer_id"]),
    customerName: text(body["customer_name"]),
    customerBalance: whole(body["customer_balance"]),
  };
}

function receiptLink(value: unknown): ReceiptLink {
  const body = record(value);
  return { url: text(body["url"]), expiresAt: text(body["expires_at"]) };
}

/** Every page has the form of the customers' page, the one the description names. */
function page<T>(item: (element: unknown) => T): (value: unknown) => Page<T> {
  return (value) => {
    const body = fieldsOf<Wire["CustomerPage"]>(value);
    return { items: list(body.raw("items"), item), nextCursor: body.get("next_cursor", textOrNull) };
  };
}

function overviewFigures(value: unknown): OverviewFigures {
  const body = fieldsOf<Wire["OverviewFigures"]>(value);
  const late = fieldsOf<Wire["OverviewOverdue"]>(body.raw("overdue"));
  return {
    outstanding: body.get("outstanding", whole),
    debtors: body.get("debtors", whole),
    overdueAmount: late.get("amount", whole),
    overdueCustomers: late.get("customers", whole),
    dueToday: body.get("due_today", whole),
  };
}

function overview(value: unknown): Overview {
  return { ...overviewFigures(value), ...dollars(fieldsOf<Wire["Overview"]>(value).raw("usd"), overviewFigures) };
}

function recordedEntry(value: unknown): RecordedEntry {
  const body = record(value);
  const made = record(body["entry"]);
  return {
    entry: {
      id: text(made["id"]),
      kind: text(made["kind"]),
      amount: whole(made["amount"]),
      promisedDate: textOrNull(made["promised_date"]),
      lines: goodsLines(made["lines"]),
      ...tagged(made["currency"]),
    },
    customer: customer(body["customer"]),
    limitWarning: limitFigures(body["limit_warning"]),
    ...stockNoted(body["stock_warnings"]),
  };
}

/** A quantity as the stock writes it: a decimal string with up to three decimals, below zero too. */
const STOCK_QTY = /^-?\d{1,12}(?:\.\d{1,3})?$/;

/** The stock's warnings on an answer, when it carries any. A kind this client does not know is left out. */
function stockNoted(value: unknown): StockNoted {
  if (value === undefined || value === null) {
    return {};
  }
  const stockWarnings: StockWarning[] = [];
  for (const element of list(value, record)) {
    const itemId = text(element["item"]);
    const name = text(element["name"]);
    if (element["kind"] === "negative") {
      const onHand = text(element["on_hand"]);
      if (!STOCK_QTY.test(onHand)) {
        throw new Malformed();
      }
      stockWarnings.push({ kind: "negative", itemId, name, onHand });
    } else if (element["kind"] === "unit") {
      stockWarnings.push({ kind: "unit", itemId, name, unit: text(element["unit"]) });
    }
  }
  return stockWarnings.length > 0 ? { stockWarnings } : {};
}

function limitFigures(value: unknown): LimitFigures | null {
  if (value === null || value === undefined) {
    return null;
  }
  const body = record(value);
  return { limit: whole(body["limit"]), balance: whole(body["balance"]) };
}

/** Two whole numbers in order, such as the smallest and the largest value the server accepts. */
function range(value: unknown): [number, number] {
  const ends = list(value, whole);
  const [first, last] = ends;
  if (ends.length !== 2 || first === undefined || last === undefined || first > last) {
    throw new Malformed();
  }
  return [first, last];
}

function creditSettings(value: unknown): CreditSettings {
  const body = record(value);
  const [min, max] = range(body["limit_bounds"]);
  return {
    defaultLimit: wholeOrNull(body["default_credit_limit"]),
    sellersMayExceed: flag(body["sellers_may_exceed"]),
    bounds: { min, max },
    ...dollars(body["usd"], (value) => {
      const inDollars = record(value);
      const [least, most] = range(inDollars["limit_bounds"]);
      return { defaultLimit: wholeOrNull(inDollars["default_credit_limit"]), bounds: { min: least, max: most } };
    }),
  };
}

/** `{ usdAmount }` from the `usd: { amount }` beside a reminder's so'm amount, when it is there. */
function usdAmount(value: unknown): { usdAmount?: number } {
  return value === undefined || value === null ? {} : { usdAmount: whole(record(value)["amount"]) };
}

function wordings(value: unknown): Record<string, string> {
  const texts: Record<string, string> = {};
  for (const [language, wording] of Object.entries(record(value))) {
    texts[language] = text(wording);
  }
  return texts;
}

function reminderSettings(value: unknown): ReminderSettings {
  const body = record(value);
  const [first, last] = range(body["hours"]);
  return {
    on: flag(body["on"]),
    hour: whole(body["hour"]),
    template: whole(body["template"]),
    smsOn: flag(body["sms_on"]),
    hours: { first, last },
    templates: list(body["templates"], (element) => {
      const template = record(element);
      return {
        id: whole(template["id"]),
        dueToday: wordings(template["due_today"]),
        overdue: wordings(template["overdue"]),
      };
    }),
  };
}

function sentReminder(value: unknown): SentReminder {
  const body = record(value);
  return { channel: text(body["channel"]), amount: whole(body["amount"]), ...usdAmount(body["usd"]) };
}

function unreachableCustomer(value: unknown): UnreachableCustomer {
  const body = record(value);
  return {
    customerId: text(body["customer_id"]),
    displayName: text(body["display_name"]),
    phone: textOrNull(body["phone"]),
    amount: whole(body["amount"]),
    ...usdAmount(body["usd"]),
  };
}

function subscription(value: unknown): Subscription {
  const body = record(value);
  return {
    state: text(body["state"]),
    plan: body["plan"] === undefined || body["plan"] === null ? null : subscriptionPlan(body["plan"]),
    endsOn: textOrNull(body["ends_on"]),
    daysLeft: wholeOrNull(body["days_left"]),
    priceUzs: whole(body["price_uzs"]),
    cardNumber: textOrNull(body["card_number"]),
    // A server from before there were several cards sends none: its one card is then the list.
    cards: body["cards"] === undefined ? [] : list(body["cards"], paymentCard),
  };
}

function subscriptionPlan(value: unknown): SubscriptionPlan {
  const body = record(value);
  const sms = record(body["sms"]);
  return {
    freeCustomers: whole(body["free_customers"]),
    customers: whole(body["customers"]),
    afterPeriod: textOrNull(body["after_period"]),
    sms: { offered: flag(sms["offered"]), included: flag(sms["included"]), quota: whole(sms["quota"]), left: whole(sms["left"]) },
  };
}

function paymentCard(value: unknown): PaymentCard {
  const body = record(value);
  return { number: text(body["number"]), label: text(body["label"]) };
}

function addedLines(value: unknown): AddedLines {
  const body = record(value);
  const made = record(body["entry"]);
  return {
    id: text(made["id"]),
    amount: whole(made["amount"]),
    lines: list(made["lines"], goodsLine),
    ...stockNoted(body["stock_warnings"]),
  };
}

function chosenPromise(value: unknown): ChosenPromise {
  const made = record(record(value)["entry"]);
  return { id: text(made["id"]), amount: whole(made["amount"]), promisedDate: text(made["promised_date"]) };
}

function catalogItem(value: unknown): CatalogItem {
  const body = record(value);
  const status = body["status"];
  if (status !== "active" && status !== "hidden") {
    throw new Malformed();
  }
  return {
    id: text(body["id"]),
    name: text(body["name"]),
    unit: text(body["unit"]),
    price: whole(body["price"]),
    learned: flag(body["learned"]),
    status,
    mergedInto: textOrNull(body["merged_into"]),
  };
}

function shopSettings(value: unknown): ShopSettings {
  const body = fieldsOf<Wire["Shop"]>(value);
  return {
    id: body.get("id", text),
    name: body.get("name", text),
    lang: body.get("lang", text),
    defaultPromiseDays: body.get("default_promise_days", whole),
    // The key itself is the platform's switch: without it the shop has no such setting at all.
    ...(typeof body.raw("usd_on") === "boolean" ? { usdOn: body.raw("usd_on") === true } : {}),
  };
}

/** The request form of goods lines. Refuses, before anything is sent, a line the server would refuse. */
function linesBody(lines: readonly NewLine[]): Wire["GoodsLine"][] {
  if (lines.length < 1 || lines.length > MAX_LINES) {
    throw new RangeError(`an entry takes 1 to ${MAX_LINES} goods lines`);
  }
  return lines.map((line) => {
    if (lineTotal(line.qty, line.unitPrice) === null) {
      throw new RangeError("a goods line needs a quantity in thousandths and a whole price in UZS");
    }
    const body: Wire["GoodsLine"] = { qty: qtyToApi(line.qty), unit_price: line.unitPrice };
    if ("catalogItemId" in line) {
      body.catalog_item_id = line.catalogItemId;
    } else {
      body.name = line.name;
      if (line.unit !== null) {
        body.unit = line.unit;
      }
    }
    return body;
  });
}

/** The header the server sends with a person's shops while the permission matrix is switched on. */
export const PERMISSIONS_HEADER = "X-Qarz-Permissions";
/** The header the server sends with a person's shops while the cash book is switched on. */
export const CASH_BOOK_HEADER = "X-Qarz-Cash-Book";
/** The header the server sends with a person's shops while the stock is switched on. */
export const STOCK_HEADER = "X-Qarz-Stock";

function myShops(value: unknown, headers: Headers): MyShops {
  const body = fieldsOf<Wire["MyShops"]>(value);
  return {
    permissionsOn: headers.get(PERMISSIONS_HEADER) === "on",
    cashBookOn: headers.get(CASH_BOOK_HEADER) === "on",
    stockOn: headers.get(STOCK_HEADER) === "on",
    items: list(body.raw("items"), (element) => {
      const shop = fieldsOf<Wire["MyShop"]>(element);
      const role = shop.raw("role");
      if (!isRole(role)) {
        throw new Malformed();
      }
      return {
        shopId: shop.get("shop_id", text),
        name: shop.get("name", text),
        role,
        membershipId: shop.get("membership_id", textOrNull),
      };
    }),
    activeShop: body.get("active_shop", textOrNull),
  };
}

function wholeOrNull(value: unknown): number | null {
  return value === null || value === undefined ? null : whole(value);
}

function dispute(value: unknown): Dispute {
  const body = record(value);
  return {
    id: text(body["id"]),
    status: text(body["status"]),
    reason: text(body["reason"]),
    declineReason: textOrNull(body["decline_reason"]),
  };
}

function openDispute(value: unknown): OpenDispute {
  const body = record(value);
  return {
    ...dispute(body),
    entryId: text(body["entry_id"]),
    createdAt: text(body["created_at"]),
    customerId: text(body["customer_id"]),
    customerName: text(body["customer_name"]),
    amount: whole(body["amount"]),
    ...tagged(body["currency"]),
  };
}

function linkState(value: unknown): LinkState {
  const body = record(value);
  return { linked: flag(body["linked"]), status: textOrNull(body["status"]), since: textOrNull(body["since"]) };
}

function issuedLink(value: unknown): IssuedLink {
  const body = record(value);
  return { start: textOrNull(body["start"]), expiresAt: textOrNull(body["expires_at"]) };
}

function counterCode(value: unknown): CounterCode {
  const body = record(value);
  return { exists: flag(body["exists"]), since: textOrNull(body["since"]) };
}

function waitingPerson(value: unknown): WaitingPerson {
  const body = record(value);
  return { id: text(body["id"]), name: text(body["name"]), since: text(body["since"]) };
}

function myAccount(value: unknown): MyAccount {
  const body = record(value);
  return {
    linkId: text(body["link_id"]),
    shopName: text(body["shop_name"]),
    displayName: text(body["display_name"]),
    balance: whole(body["balance"]),
    ...dollars(body["usd"], (value) => ({ balance: whole(record(value)["balance"]) })),
  };
}

function accountEntry(value: unknown): AccountEntry {
  const body = record(value);
  const objection = body["dispute"];
  return {
    id: text(body["id"]),
    kind: text(body["kind"]),
    amount: whole(body["amount"]),
    createdAt: text(body["created_at"]),
    promisedDate: textOrNull(body["promised_date"]),
    reversesId: textOrNull(body["reverses_id"]),
    reversed: flag(body["reversed"]),
    disputed: flag(body["disputed"]),
    dispute: objection === null || objection === undefined ? null : dispute(objection),
    lines: goodsLines(body["lines"]),
    promises: promiseRecords(body["promises"]),
    dateRequest: dateRequestOrNull(body["date_request"]),
    ...tagged(body["currency"]),
  };
}

function accountDollars(value: unknown): AccountDollars {
  const body = record(value);
  const late = record(body["overdue"]);
  return {
    balance: whole(body["balance"]),
    overdueAmount: whole(late["amount"]),
    dueToday: whole(late["due_today"]),
    paymentHistory: paymentHistory(body["payment_history"]),
  };
}

function accountDetail(value: unknown): AccountDetail {
  const body = record(value);
  const late = record(body["overdue"]);
  return {
    linkId: text(body["link_id"]),
    shopName: text(body["shop_name"]),
    displayName: text(body["display_name"]),
    balance: whole(body["balance"]),
    ...dollars(body["usd"], accountDollars),
    overdueAmount: whole(late["amount"]),
    dueToday: whole(late["due_today"]),
    removalRequested: flag(body["removal_requested"]),
    paymentHistory: paymentHistory(body["payment_history"]),
    entries: list(body["entries"], accountEntry),
    entriesTotal: whole(body["entries_total"]),
    paymentNotices: paymentNotices(body["payment_notices"]),
  };
}

function removalOutcome(value: unknown): RemovalOutcome {
  const body = record(value);
  const inDollars = body["usd"];
  return {
    removed: flag(body["removed"]),
    waitingForBalance: wholeOrNull(body["waiting_for_balance"]),
    ...(inDollars === undefined || inDollars === null
      ? {}
      : { waitingForUsd: whole(record(inDollars)["waiting_for_balance"]) }),
  };
}

function items<T>(item: (element: unknown) => T): (value: unknown) => T[] {
  return (value) => list(record(value)["items"], item);
}

/** The request form of a reason. Refuses, before anything is sent, one the server would refuse. */
function reasonBody(reason: string): string {
  const clean = cleanReason(reason);
  if (clean === null) {
    throw new RangeError(`a reason takes ${REASON_MIN} to ${REASON_MAX} characters`);
  }
  return clean;
}

/** What a status means when the answer carries no code of its own (a proxy's page in front of the API). */
const STATUS_CODES: Readonly<Record<number, string>> = { 413: "BODY_TOO_LARGE", 429: "RATE_LIMITED" };

/** `Retry-After` as whole seconds; null when it is absent or is not a number of seconds. */
function retryAfter(response: Response): number | null {
  const raw = response.headers.get("Retry-After")?.trim() ?? "";
  return /^\d{1,6}$/.test(raw) ? Number(raw) : null;
}

async function errorFrom(response: Response): Promise<ApiError> {
  const wait = retryAfter(response);
  try {
    const failure = record(record(await response.json())["error"]);
    const fields: Record<string, string> = {};
    for (const [name, message] of Object.entries(record(failure["fields"] ?? {}))) {
      fields[name] = String(message);
    }
    return new ApiError(response.status, text(failure["code"]), textOrNull(failure["message"]), fields, wait);
  } catch {
    // A proxy's HTML error page, for example: there is no message worth showing. The status still says
    // what happened when it is one of the three a person can act on.
    return new ApiError(response.status, STATUS_CODES[response.status] ?? "ERROR", null, {}, wait);
  }
}

/** The response readers, for modules that add calls of their own (see `call`). */
export const reading = {
  record,
  text,
  textOrNull,
  whole,
  wholeOrNull,
  flag,
  list,
  fieldsOf,
  page,
  items,
  /** A customer as a list names one, and with the entries of their page: read by more than one API. */
  customer,
  customerDetail,
};

// --- requests ----------------------------------------------------------------------------------------

const IDEMPOTENCY_KEY = /^[A-Za-z0-9_-]{8,128}$/;

export type Call<T> = {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  query?: Readonly<Record<string, string | null | undefined>>;
  body?: unknown;
  /** A multipart form in place of a JSON body: the browser writes its content type and boundary. */
  form?: FormData;
  /** A file as the whole request body, for the one route that takes it so (the import's upload). */
  raw?: Blob;
  /** The answer is a file, not JSON: `read` is given its `Blob` (the import's template). */
  binary?: boolean;
  idempotencyKey?: string;
  signal?: AbortSignal | undefined;
  /** Reads the answer's body; the headers are there for the one answer that says something in them. */
  read: (value: unknown, headers: Headers) => T;
};

export type Transport = {
  fetch: Fetch;
  auth: ApiAuth | null;
  onUnauthenticated?: (() => void) | undefined;
  /** Told of every refusal, whoever asked: the shell learns from it that the shop is limited or suspended. */
  onRefusal?: ((error: ApiError) => void) | undefined;
};

/**
 * One request. Exported for the modules that add calls outside the first load (the web panel's sign-in
 * and back office); they go through the same headers, errors and refusal hooks as every other call.
 */
export async function call<T>(transport: Transport, request: Call<T>): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (transport.auth?.kind === "bearer") {
    headers["Authorization"] = `Bearer ${transport.auth.token}`;
  } else if (transport.auth?.kind === "cookie" && transport.auth.csrfToken && request.method !== "GET") {
    headers["X-CSRF-Token"] = transport.auth.csrfToken;
  }
  if (request.idempotencyKey !== undefined) {
    if (!IDEMPOTENCY_KEY.test(request.idempotencyKey)) {
      throw new RangeError("idempotency key must be 8 to 128 characters of A-Z a-z 0-9 _ -");
    }
    headers["Idempotency-Key"] = request.idempotencyKey;
  }
  const init: RequestInit = {
    method: request.method,
    headers,
    credentials: transport.auth?.kind === "cookie" ? "same-origin" : "omit",
  };
  if (request.form !== undefined) {
    init.body = request.form;
  } else if (request.raw !== undefined) {
    init.body = request.raw;
  } else if (request.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(request.body);
  }
  if (request.signal) {
    init.signal = request.signal;
  }
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(request.query ?? {})) {
    if (value !== null && value !== undefined && value !== "") {
      search.set(name, value);
    }
  }
  const url = search.size > 0 ? `${request.path}?${search.toString()}` : request.path;

  let response: Response;
  try {
    response = await transport.fetch(url, init);
  } catch (error) {
    if (isAbort(error)) {
      throw error;
    }
    throw new ApiError(0, NETWORK_ERROR, null);
  }
  if (!response.ok) {
    const failure = await errorFrom(response);
    if (response.status === 401) {
      transport.onUnauthenticated?.();
    }
    transport.onRefusal?.(failure);
    throw failure;
  }
  try {
    if (request.binary) {
      return request.read(await response.blob(), response.headers);
    }
    return request.read(response.status === 204 ? null : await response.json(), response.headers);
  } catch (error) {
    if (isAbort(error)) {
      throw error;
    }
    throw new ApiError(response.status, BAD_RESPONSE, null);
  }
}

/** Exchanges the Mini App's signed launch string for a session token. The string is a credential. */
export async function signInWebApp(fetch: Fetch, initData: string): Promise<string> {
  return call(
    { fetch, auth: null },
    {
      method: "POST",
      path: "/api/v1/auth/telegram-webapp",
      body: { init_data: initData },
      read: (value) => text(record(value)["token"]),
    },
  );
}

const segment = encodeURIComponent;

function shopApi(transport: Transport, shopId: string) {
  const base = `/api/v1/shops/${segment(shopId)}`;
  return {
    /** Path of the shop in the API, for calls added by a module that is loaded apart (see `send`). */
    base,

    /** Sends a request with this session: the same headers, CSRF token and refusal hooks as the rest. */
    send<T>(request: Call<T>): Promise<T> {
      return call(transport, request);
    },

    /**
     * What the signed-in member may do in this shop (keys of the server's permission catalogue), or null
     * when the server keeps to roles: the route exists only while the permission matrix is switched on.
     */
    myPermissions(signal?: AbortSignal): Promise<ReadonlySet<string> | null> {
      return call(transport, {
        method: "GET",
        path: `${base}/permissions/mine`,
        signal,
        read: (value): ReadonlySet<string> => new Set(list(record(value)["permissions"], text)),
      }).catch((error: unknown) => {
        if (toApiError(error).status === 404) {
          return null;
        }
        throw error;
      });
    },

    listCustomers(
      params: { q?: string; status?: "active" | "archived"; cursor?: string | null; limit?: number },
      signal?: AbortSignal,
    ): Promise<Page<Customer>> {
      return call(transport, {
        method: "GET",
        path: `${base}/customers`,
        query: { q: params.q, status: params.status, cursor: params.cursor, limit: params.limit?.toString() },
        signal,
        read: page(customer),
      });
    },

    createCustomer(input: { displayName: string; phone: string | null }, idempotencyKey: string): Promise<Customer> {
      const body: Wire["NewCustomer"] = { display_name: input.displayName };
      if (input.phone !== null) {
        body.phone = input.phone;
      }
      return call(transport, { method: "POST", path: `${base}/customers`, body, idempotencyKey, read: customer });
    },

    readCustomer(customerId: string, signal?: AbortSignal): Promise<CustomerDetail> {
      return call(transport, {
        method: "GET",
        path: `${base}/customers/${segment(customerId)}`,
        signal,
        read: customerDetail,
      });
    },

    updateCustomer(customerId: string, patch: CustomerPatch, idempotencyKey: string): Promise<Customer> {
      const body: Wire["CustomerPatch"] = {};
      if (patch.displayName !== undefined) {
        body.display_name = patch.displayName;
      }
      if (patch.phone !== undefined) {
        body.phone = patch.phone; // null removes the number
      }
      if (patch.remindersOff !== undefined) {
        body.reminders_off = patch.remindersOff;
      }
      if (patch.creditLimit !== undefined) {
        if (patch.creditLimit !== null && !Number.isSafeInteger(patch.creditLimit)) {
          throw new RangeError("a credit limit must be a whole number of UZS");
        }
        body.credit_limit = patch.creditLimit; // null removes the customer's own limit
      }
      if (patch.creditLimitUsd !== undefined) {
        if (patch.creditLimitUsd !== null && !Number.isSafeInteger(patch.creditLimitUsd)) {
          throw new RangeError("a dollar credit limit must be a whole number of cents");
        }
        body.credit_limit_usd = patch.creditLimitUsd;
      }
      return call(transport, {
        method: "PATCH",
        path: `${base}/customers/${segment(customerId)}`,
        body,
        idempotencyKey,
        read: customer,
      });
    },

    setArchived(customerId: string, archived: boolean, idempotencyKey: string): Promise<Customer> {
      return call(transport, {
        method: "POST",
        path: `${base}/customers/${segment(customerId)}/${archived ? "archive" : "unarchive"}`,
        idempotencyKey,
        read: customer,
      });
    },

    recordEntry(customerId: string, input: NewEntry, idempotencyKey: string): Promise<RecordedEntry> {
      const body: Wire["NewEntry"] = { kind: input.kind };
      if (input.lines !== undefined) {
        // The type says so too; a caller that got around it must not send goods priced in so'm as dollars.
        if (input.kind !== "credit" || (input as { currency?: Currency }).currency === "USD") {
          throw new RangeError("only a credit sale in so'm has goods lines");
        }
        body.lines = linesBody(input.lines);
      } else if (Number.isSafeInteger(input.amount)) {
        body.amount = input.amount;
        if (input.currency === "USD") {
          body.currency = "USD"; // so'm is the absence of the field, as before dollars existed
        }
      } else {
        throw new RangeError("amount must be a whole number of UZS, or of cents");
      }
      if (input.note !== null) {
        body.note = input.note;
      }
      if (input.promisedDate !== null) {
        body.promised_date = input.promisedDate;
      }
      if (input.method !== undefined) {
        if (input.kind !== "payment") {
          throw new RangeError("only a payment has a method");
        }
        body.method = input.method;
      }
      return call(transport, {
        method: "POST",
        path: `${base}/customers/${segment(customerId)}/entries`,
        body,
        idempotencyKey,
        read: recordedEntry,
      });
    },

    reverseEntry(entryId: string, idempotencyKey: string): Promise<Customer> {
      return call(transport, {
        method: "POST",
        path: `${base}/entries/${segment(entryId)}/reversal`,
        idempotencyKey,
        read: (value) => customer(record(value)["customer"]),
      });
    },

    /** Adds goods to an amount-only credit sale, once (REQ-038). */
    addLines(entryId: string, lines: readonly NewLine[], idempotencyKey: string): Promise<AddedLines> {
      return call(transport, {
        method: "POST",
        path: `${base}/entries/${segment(entryId)}/lines`,
        body: { lines: linesBody(lines) } satisfies Wire["NewLines"],
        idempotencyKey,
        read: addedLines,
      });
    },

    /** The one-tap promised date right after a sale that was saved with the shop's usual term (REQ-008). */
    choosePromise(entryId: string, promisedDate: string, idempotencyKey: string): Promise<ChosenPromise> {
      return call(transport, {
        method: "POST",
        path: `${base}/entries/${segment(entryId)}/promise-choice`,
        body: { promised_date: promisedDate } satisfies Wire["PromiseChoice"],
        idempotencyKey,
        read: chosenPromise,
      });
    },

    /**
     * A manager or an owner moves the promised date of a debt that still stands (REQ-067). The date it
     * replaces stays in the entry's history.
     */
    changePromise(
      entryId: string,
      promisedDate: string,
      reason: string | null,
      idempotencyKey: string,
    ): Promise<ChangedPromise> {
      const body: Wire["PromiseChange"] = { promised_date: promisedDate };
      if (reason !== null) {
        body.reason = reason;
      }
      return call(transport, {
        method: "POST",
        path: `${base}/entries/${segment(entryId)}/promise`,
        body,
        idempotencyKey,
        read: changedPromise,
      });
    },

    /** Payment notices that wait for a decision. Every member of staff may list and decide them (REQ-061). */
    listPaymentNotices(signal?: AbortSignal): Promise<OpenPaymentNotice[]> {
      return call(transport, { method: "GET", path: `${base}/payment-notices`, signal, read: items(openPaymentNotice) });
    },

    /**
     * Accepts a notice, which records a payment. `amount` corrects what the customer stated; null
     * records the stated amount. `method` says how the money came, and only while the cash book is on:
     * otherwise the server does not know the field.
     */
    acceptPaymentNotice(
      noticeId: string,
      amount: number | null,
      idempotencyKey: string,
      method?: PaymentMethod,
    ): Promise<void> {
      if (amount !== null && !Number.isSafeInteger(amount)) {
        throw new RangeError("amount must be a whole number of UZS");
      }
      return call(transport, {
        method: "POST",
        path: `${base}/payment-notices/${segment(noticeId)}/accept`,
        body: { ...(amount === null ? {} : { amount }), ...(method === undefined ? {} : { method }) },
        idempotencyKey,
        read: () => undefined,
      });
    },

    /** Declines a notice; the reason is sent to the customer. */
    declinePaymentNotice(noticeId: string, reason: string, idempotencyKey: string): Promise<void> {
      return call(transport, {
        method: "POST",
        path: `${base}/payment-notices/${segment(noticeId)}/decline`,
        body: { reason: reasonBody(reason) },
        idempotencyKey,
        read: () => undefined,
      });
    },

    /**
     * Asks, with the session, for a link to a notice's receipt. The link works for five minutes without
     * the session, in a new tab; when it has run out it answers 404 and a new one is asked for here.
     */
    receiptLink(noticeId: string, signal?: AbortSignal): Promise<ReceiptLink> {
      return call(transport, {
        method: "GET",
        path: `${base}/payment-notices/${segment(noticeId)}/receipt`,
        signal,
        read: receiptLink,
      });
    },

    /** Open requests to move a promised date. Managers and owners only (REQ-067). */
    listDateRequests(signal?: AbortSignal): Promise<OpenDateRequest[]> {
      return call(transport, { method: "GET", path: `${base}/date-requests`, signal, read: items(openDateRequest) });
    },

    /** Accepts a request: the date asked for becomes the entry's promised date. */
    acceptDateRequest(requestId: string, idempotencyKey: string): Promise<void> {
      return call(transport, {
        method: "POST",
        path: `${base}/date-requests/${segment(requestId)}/accept`,
        idempotencyKey,
        read: () => undefined,
      });
    },

    /** Declines a request; the reason, when one is given, is sent to the customer. */
    declineDateRequest(requestId: string, reason: string | null, idempotencyKey: string): Promise<void> {
      return call(transport, {
        method: "POST",
        path: `${base}/date-requests/${segment(requestId)}/decline`,
        body: reason === null ? {} : { reason },
        idempotencyKey,
        read: () => undefined,
      });
    },

    listCatalog(
      params: { q?: string; status?: "active" | "hidden"; learned?: boolean; cursor?: string | null; limit?: number },
      signal?: AbortSignal,
    ): Promise<Page<CatalogItem>> {
      return call(transport, {
        method: "GET",
        path: `${base}/catalog`,
        query: {
          q: params.q,
          status: params.status,
          learned: params.learned === undefined ? undefined : String(params.learned),
          cursor: params.cursor,
          limit: params.limit?.toString(),
        },
        signal,
        read: page(catalogItem),
      });
    },

    createCatalogItem(input: NewCatalogItem, idempotencyKey: string): Promise<CatalogItem> {
      if (!Number.isSafeInteger(input.price)) {
        throw new RangeError("price must be a whole number of UZS");
      }
      const body: Json = { name: input.name, price: input.price };
      if (input.unit !== null) {
        body["unit"] = input.unit;
      }
      return call(transport, { method: "POST", path: `${base}/catalog`, body, idempotencyKey, read: catalogItem });
    },

    updateCatalogItem(itemId: string, patch: CatalogItemPatch, idempotencyKey: string): Promise<CatalogItem> {
      if (patch.price !== undefined && !Number.isSafeInteger(patch.price)) {
        throw new RangeError("price must be a whole number of UZS");
      }
      const body: Json = {};
      if (patch.name !== undefined) {
        body["name"] = patch.name;
      }
      if (patch.unit !== undefined) {
        body["unit"] = patch.unit; // an empty unit becomes the default one, a piece
      }
      if (patch.price !== undefined) {
        body["price"] = patch.price;
      }
      return call(transport, {
        method: "PATCH",
        path: `${base}/catalog/${segment(itemId)}`,
        body,
        idempotencyKey,
        read: catalogItem,
      });
    },

    /** Hide or show an item, or settle a learned one: accept it as it is, or dismiss it from the catalog. */
    changeCatalogItem(itemId: string, action: CatalogAction, idempotencyKey: string): Promise<CatalogItem> {
      return call(transport, {
        method: "POST",
        path: `${base}/catalog/${segment(itemId)}/${action}`,
        idempotencyKey,
        read: catalogItem,
      });
    },

    /** A learned item is another spelling of `intoId`: it becomes a hidden alias of that item. */
    mergeCatalogItem(itemId: string, intoId: string, idempotencyKey: string): Promise<CatalogItem> {
      return call(transport, {
        method: "POST",
        path: `${base}/catalog/${segment(itemId)}/merge`,
        body: { into: intoId },
        idempotencyKey,
        read: catalogItem,
      });
    },

    /** Whether the customer is connected to a Telegram account (REQ-013). */
    readLink(customerId: string, signal?: AbortSignal): Promise<LinkState> {
      return call(transport, {
        method: "GET",
        path: `${base}/customers/${segment(customerId)}/link`,
        signal,
        read: linkState,
      });
    },

    /** Issues the customer's personal link. The answer carries the start code once. */
    createLink(customerId: string, idempotencyKey: string): Promise<IssuedLink> {
      return call(transport, {
        method: "POST",
        path: `${base}/customers/${segment(customerId)}/link`,
        idempotencyKey,
        read: issuedLink,
      });
    },

    readCounterCode(signal?: AbortSignal): Promise<CounterCode> {
      return call(transport, { method: "GET", path: `${base}/counter-code`, signal, read: counterCode });
    },

    /** Issues the shop's counter code and cancels the one before it. Managers and owners only. */
    rotateCounterCode(idempotencyKey: string): Promise<IssuedLink> {
      return call(transport, { method: "POST", path: `${base}/counter-code`, idempotencyKey, read: issuedLink });
    },

    listWaiting(signal?: AbortSignal): Promise<WaitingPerson[]> {
      return call(transport, { method: "GET", path: `${base}/waiting`, signal, read: items(waitingPerson) });
    },

    attachWaiting(waitingId: string, customerId: string, idempotencyKey: string): Promise<void> {
      return call(transport, {
        method: "POST",
        path: `${base}/waiting/${segment(waitingId)}/attach`,
        body: { customer_id: customerId },
        idempotencyKey,
        read: () => undefined,
      });
    },

    dismissWaiting(waitingId: string, idempotencyKey: string): Promise<void> {
      return call(transport, {
        method: "POST",
        path: `${base}/waiting/${segment(waitingId)}/dismiss`,
        idempotencyKey,
        read: () => undefined,
      });
    },

    /** Open disputes of the shop. Managers and owners only (REQ-017). */
    listDisputes(signal?: AbortSignal): Promise<OpenDispute[]> {
      return call(transport, { method: "GET", path: `${base}/disputes`, signal, read: items(openDispute) });
    },

    /** Declines a dispute; the reason is sent to the customer. */
    declineDispute(disputeId: string, reason: string, idempotencyKey: string): Promise<void> {
      return call(transport, {
        method: "POST",
        path: `${base}/disputes/${segment(disputeId)}/decline`,
        body: { reason: reasonBody(reason) },
        idempotencyKey,
        read: () => undefined,
      });
    },

    readSettings(signal?: AbortSignal): Promise<ShopSettings> {
      return call(transport, { method: "GET", path: base, signal, read: shopSettings });
    },

    updateSettings(patch: ShopSettingsPatch, idempotencyKey: string): Promise<ShopSettings> {
      const body: Json = {};
      if (patch.name !== undefined) {
        body["name"] = patch.name;
      }
      if (patch.lang !== undefined) {
        body["lang"] = patch.lang;
      }
      if (patch.defaultPromiseDays !== undefined) {
        if (!Number.isSafeInteger(patch.defaultPromiseDays)) {
          throw new RangeError("default promise days must be a whole number");
        }
        body["default_promise_days"] = patch.defaultPromiseDays;
      }
      if (patch.usdOn !== undefined) {
        body["usd_on"] = patch.usdOn;
      }
      return call(transport, { method: "PATCH", path: base, body, idempotencyKey, read: shopSettings });
    },

    /** The shop's credit rules. Every member of staff may read them (REQ-044). */
    readCreditSettings(signal?: AbortSignal): Promise<CreditSettings> {
      return call(transport, { method: "GET", path: `${base}/credit-settings`, signal, read: creditSettings });
    },

    /** Managers and owners only. A null default limit removes it. */
    updateCreditSettings(patch: CreditSettingsPatch, idempotencyKey: string): Promise<CreditSettings> {
      const body: Json = {};
      if (patch.defaultLimit !== undefined) {
        if (patch.defaultLimit !== null && !Number.isSafeInteger(patch.defaultLimit)) {
          throw new RangeError("a credit limit must be a whole number of UZS");
        }
        body["default_credit_limit"] = patch.defaultLimit;
      }
      if (patch.defaultLimitUsd !== undefined) {
        if (patch.defaultLimitUsd !== null && !Number.isSafeInteger(patch.defaultLimitUsd)) {
          throw new RangeError("a dollar credit limit must be a whole number of cents");
        }
        body["default_credit_limit_usd"] = patch.defaultLimitUsd;
      }
      if (patch.sellersMayExceed !== undefined) {
        body["sellers_may_exceed"] = patch.sellersMayExceed;
      }
      return call(transport, {
        method: "PATCH",
        path: `${base}/credit-settings`,
        body,
        idempotencyKey,
        read: creditSettings,
      });
    },

    /** Reminder settings with the wordings to choose from. Managers and owners only (REQ-042). */
    readReminders(signal?: AbortSignal): Promise<ReminderSettings> {
      return call(transport, { method: "GET", path: `${base}/reminders`, signal, read: reminderSettings });
    },

    updateReminders(patch: ReminderSettingsPatch, idempotencyKey: string): Promise<ReminderSettings> {
      const body: Json = {};
      if (patch.on !== undefined) {
        body["on"] = patch.on;
      }
      for (const [name, value] of [
        ["hour", patch.hour],
        ["template", patch.template],
      ] as const) {
        if (value !== undefined) {
          if (!Number.isSafeInteger(value)) {
            throw new RangeError(`${name} must be a whole number`);
          }
          body[name] = value;
        }
      }
      if (patch.smsOn !== undefined) {
        body["sms_on"] = patch.smsOn;
      }
      return call(transport, { method: "PATCH", path: `${base}/reminders`, body, idempotencyKey, read: reminderSettings });
    },

    /** Sends one reminder now. The server allows one a day per customer (REQ-025). */
    sendReminder(customerId: string, idempotencyKey: string): Promise<SentReminder> {
      return call(transport, {
        method: "POST",
        path: `${base}/reminders/manual`,
        body: { customer_id: customerId },
        idempotencyKey,
        read: sentReminder,
      });
    },

    listUnreachable(signal?: AbortSignal): Promise<UnreachableCustomer[]> {
      return call(transport, {
        method: "GET",
        path: `${base}/reminders/unreachable`,
        signal,
        read: items(unreachableCustomer),
      });
    },

    /** The owner only; the owner may read it in every mode, because it says how to leave the mode. */
    readSubscription(signal?: AbortSignal): Promise<Subscription> {
      return call(transport, { method: "GET", path: `${base}/subscription`, signal, read: subscription });
    },

    overview(signal?: AbortSignal): Promise<Overview> {
      return call(transport, { method: "GET", path: `${base}/overview`, signal, read: overview });
    },

    /** Who owes, largest debt first: by the so'm debt, or with `currency: "USD"` by the dollar debt. */
    debtors(
      params: { overdue: boolean; cursor?: string | null; limit?: number; currency?: Currency },
      signal?: AbortSignal,
    ): Promise<Page<Debtor>> {
      return call(transport, {
        method: "GET",
        path: `${base}/overview/debtors`,
        query: {
          overdue: params.overdue ? "true" : "false",
          cursor: params.cursor,
          limit: params.limit?.toString(),
          // So'm is the server's default and is not named, so the request is the one it always was.
          currency: params.currency === "USD" ? "USD" : undefined,
        },
        signal,
        read: page(debtor),
      });
    },
  };
}

export type ShopApi = ReturnType<typeof shopApi>;

/**
 * A person's own customer account, behind their link. These writes take no Idempotency-Key: each one
 * ends a state (a link, an open dispute) that the server refuses to end twice.
 */
function accountApi(transport: Transport, linkId: string) {
  const base = `/api/v1/me/accounts/${segment(linkId)}`;
  return {
    read(signal?: AbortSignal): Promise<AccountDetail> {
      return call(transport, { method: "GET", path: base, signal, read: accountDetail });
    },
    disconnect(): Promise<void> {
      return call(transport, { method: "POST", path: `${base}/disconnect`, read: () => undefined });
    },
    requestRemoval(): Promise<RemovalOutcome> {
      return call(transport, { method: "POST", path: `${base}/removal`, read: removalOutcome });
    },
    openDispute(entryId: string, reason: string): Promise<Dispute> {
      return call(transport, {
        method: "POST",
        path: `${base}/disputes`,
        body: { entry_id: entryId, reason: reasonBody(reason) },
        read: dispute,
      });
    },
    /**
     * Tells the shop an amount was paid (REQ-060), with a receipt when there is one: then the request is
     * a multipart form with the fields `amount` and `receipt`, otherwise JSON. The key makes a repeat
     * return the first notice instead of sending a second.
     */
    sendPaymentNotice(
      amount: number,
      receipt: Blob | null,
      idempotencyKey: string,
      currency: Currency = "UZS",
    ): Promise<PaymentNotice> {
      if (!Number.isSafeInteger(amount)) {
        throw new RangeError("amount must be a whole number of UZS, or of cents");
      }
      const path = `${base}/payment-notices`;
      // Dollars are named, with the amount in cents; so'm is the absence of the field, as it always was.
      const inDollars = currency === "USD";
      if (receipt === null) {
        const body = inDollars ? { amount, currency } : { amount };
        return call(transport, { method: "POST", path, body, idempotencyKey, read: paymentNotice });
      }
      const form = new FormData();
      form.set("amount", String(amount));
      if (inDollars) {
        form.set("currency", currency);
      }
      form.set("receipt", receipt);
      return call(transport, { method: "POST", path, form, idempotencyKey, read: paymentNotice });
    },
    /** Asks the shop to move the promised date of one entry to a later day (REQ-066). */
    openDateRequest(entryId: string, requestedDate: string, reason: string | null): Promise<DateRequest> {
      const body: Json = { entry_id: entryId, requested_date: requestedDate };
      if (reason !== null) {
        body["reason"] = reason;
      }
      return call(transport, { method: "POST", path: `${base}/date-requests`, body, read: dateRequest });
    },
    withdrawDispute(disputeId: string): Promise<Dispute> {
      return call(transport, {
        method: "POST",
        path: `${base}/disputes/${segment(disputeId)}/withdraw`,
        read: dispute,
      });
    },
  };
}

export type AccountApi = ReturnType<typeof accountApi>;

export function createApi(transport: {
  fetch: Fetch;
  auth: ApiAuth;
  onUnauthenticated?: () => void;
  onRefusal?: (error: ApiError) => void;
}) {
  return {
    myShops(signal?: AbortSignal): Promise<MyShops> {
      return call(transport, { method: "GET", path: "/api/v1/me/shops", signal, read: myShops });
    },
    setActiveShop(shopId: string): Promise<void> {
      return call(transport, {
        method: "PUT",
        path: "/api/v1/me/active-shop",
        body: { shop_id: shopId },
        read: () => undefined,
      });
    },
    shop(shopId: string): ShopApi {
      return shopApi(transport, shopId);
    },
    /** Ends every session of the signed-in person, on every device, this one included. */
    signOutEverywhere(): Promise<void> {
      return call(transport, { method: "POST", path: "/api/v1/auth/sign-out-everywhere", read: () => undefined });
    },
    /** The accounts where the signed-in person is a customer (REQ-019). */
    myAccounts(signal?: AbortSignal): Promise<MyAccount[]> {
      return call(transport, { method: "GET", path: "/api/v1/me/accounts", signal, read: items(myAccount) });
    },
    account(linkId: string): AccountApi {
      return accountApi(transport, linkId);
    },
  };
}

export type Api = ReturnType<typeof createApi>;

/** One key per user action; the same key is sent again when that action is retried. */
export function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // randomUUID needs a secure context; getRandomValues does not.
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
