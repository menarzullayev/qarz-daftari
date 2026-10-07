import { lineTotal, MAX_LINES, qtyToApi, readServerQty } from "./goods";
import { isRole, type Role } from "./navigation";

/**
 * Client for the API (/api/v1): the staff workspace and a customer's own accounts. The server is the
 * authority: it checks the role, the amounts and the dates on every call. This module only shapes
 * requests and refuses a response that does not look like the contract, so a screen never shows a
 * balance that is not a whole number of UZS.
 */

export type Fetch = (input: string, init: RequestInit) => Promise<Response>;

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

  constructor(status: number, code: string, serverMessage: string | null, fields: Readonly<Record<string, string>> = {}) {
    super(code);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.serverMessage = serverMessage;
    this.fields = fields;
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
};

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
};

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
};

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
};

export type Overview = {
  outstanding: number;
  debtors: number;
  overdueAmount: number;
  overdueCustomers: number;
  dueToday: number;
};

export type Page<T> = { items: T[]; nextCursor: string | null };

export type EntryKind = "credit" | "payment";
/** With `lines` the amount is not sent: the server uses the sum of the lines (REQ-037). */
export type NewEntry = { kind: EntryKind; note: string | null; promisedDate: string | null } & (
  | { amount: number; lines?: never }
  | { lines: readonly NewLine[]; amount?: never }
);
export type RecordedEntry = {
  entry: { id: string; kind: string; amount: number; promisedDate: string | null; lines: GoodsLine[] };
  /** The customer after the entry; `balance` is the new balance. */
  customer: Customer;
  /** Set when the sale was saved although it took the balance above the limit that applies (REQ-044). */
  limitWarning: LimitFigures | null;
};

/** A credit limit and the balance that met it, both in whole UZS. */
export type LimitFigures = { limit: number; balance: number };

/** `creditLimit`: a number sets the customer's own limit, null removes it, absent leaves it. */
export type CustomerPatch = {
  displayName?: string;
  phone?: string | null;
  remindersOff?: boolean;
  creditLimit?: number | null;
};

/** The shop's rules for selling on credit (REQ-044). `bounds` are what the server accepts as a limit. */
export type CreditSettings = {
  defaultLimit: number | null;
  sellersMayExceed: boolean;
  bounds: { min: number; max: number };
};
export type CreditSettingsPatch = { defaultLimit?: number | null; sellersMayExceed?: boolean };

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
export type SentReminder = { channel: string; amount: number };

/** A customer with something due and no channel to be reminded through (REQ-043). */
export type UnreachableCustomer = { customerId: string; displayName: string; phone: string | null; amount: number };

/** The shop's subscription as its owner sees it. `state`: trial, active, limited or suspended. */
export type Subscription = {
  state: string;
  /** The last day of the trial or paid period, as an ISO date; null when no period is running. */
  endsOn: string | null;
  daysLeft: number | null;
  priceUzs: number;
  /** Where to transfer the payment; null until the administrator has set it. */
  cardNumber: string | null;
};

export type AddedLines = { id: string; amount: number; lines: GoodsLine[] };
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

export type ShopSettings = { id: string; name: string; lang: string; defaultPromiseDays: number };
export type ShopSettingsPatch = { name?: string; lang?: string; defaultPromiseDays?: number };

export type ShopMembership = {
  shopId: string;
  name: string;
  role: Role;
  /** The signed-in person's membership in this shop; null when the server does not say. */
  membershipId: string | null;
};
export type MyShops = { items: ShopMembership[]; activeShop: string | null };

/** A customer's objection to one entry. `status`: open, declined, withdrawn, or reversed (the shop agreed). */
export type Dispute = { id: string; status: string; reason: string; declineReason: string | null };

/** An open dispute as a manager or an owner sees it. `amount` is the disputed entry's. */
export type OpenDispute = Dispute & {
  entryId: string;
  createdAt: string;
  customerId: string;
  customerName: string;
  amount: number;
};

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
export type MyAccount = { linkId: string; shopName: string; displayName: string; balance: number };

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
};

export type AccountDetail = MyAccount & {
  overdueAmount: number;
  dueToday: number;
  removalRequested: boolean;
  entries: AccountEntry[];
  entriesTotal: number;
};

/** `removed`: the data is gone now. Otherwise it waits until `waitingForBalance` UZS are paid. */
export type RemovalOutcome = { removed: boolean; waitingForBalance: number | null };

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

function customer(value: unknown): Customer {
  const body = record(value);
  return {
    id: text(body["id"]),
    displayName: text(body["display_name"]),
    phone: textOrNull(body["phone"]),
    status: text(body["status"]),
    remindersOff: flag(body["reminders_off"]),
    creditLimit: wholeOrNull(body["credit_limit"]),
    balance: whole(body["balance"]),
  };
}

function overdue(value: unknown): Overdue {
  const body = record(value);
  return {
    amount: whole(body["amount"]),
    since: textOrNull(body["since"]),
    days: whole(body["days"]),
    dueToday: whole(body["due_today"]),
  };
}

function debtor(value: unknown): Debtor {
  return { ...customer(value), overdue: overdue(record(value)["overdue"]) };
}

function goodsLine(value: unknown): GoodsLine {
  const body = record(value);
  const qty = readServerQty(text(body["qty"]));
  if (qty === null) {
    throw new Malformed();
  }
  return {
    lineNo: whole(body["line_no"]),
    catalogItemId: textOrNull(body["catalog_item_id"]),
    name: text(body["name"]),
    qty,
    unit: text(body["unit"]),
    unitPrice: whole(body["unit_price"]),
    lineTotal: whole(body["line_total"]),
  };
}

/** A server that does not send goods lines yet answers without the field: that is an entry with none. */
function goodsLines(value: unknown): GoodsLine[] {
  return value === undefined || value === null ? [] : list(value, goodsLine);
}

function promiseRecord(value: unknown): PromiseRecord {
  const body = record(value);
  return {
    promisedDate: text(body["promised_date"]),
    actor: text(body["actor"]),
    reason: textOrNull(body["reason"]),
    createdAt: text(body["created_at"]),
  };
}

/** A server that does not send the history yet answers without the field: that is an entry with none. */
function promiseRecords(value: unknown): PromiseRecord[] {
  return value === undefined || value === null ? [] : list(value, promiseRecord);
}

function dateRequest(value: unknown): DateRequest {
  const body = record(value);
  return {
    id: text(body["id"]),
    entryId: text(body["entry_id"]),
    status: text(body["status"]),
    requestedDate: text(body["requested_date"]),
    reason: textOrNull(body["reason"]),
    declineReason: textOrNull(body["decline_reason"]),
    createdAt: text(body["created_at"]),
    closedAt: textOrNull(body["closed_at"]),
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
  const body = record(value);
  return {
    id: text(body["id"]),
    seq: whole(body["seq"]),
    kind: text(body["kind"]),
    amount: whole(body["amount"]),
    note: textOrNull(body["note"]),
    createdAt: text(body["created_at"]),
    promisedDate: textOrNull(body["promised_date"]),
    reversesId: textOrNull(body["reverses_id"]),
    reversed: flag(body["reversed"]),
    disputed: flag(body["disputed"]),
    authorId: textOrNull(body["author_id"]),
    lines: goodsLines(body["lines"]),
    promises: promiseRecords(body["promises"]),
    dateRequest: dateRequestOrNull(body["date_request"]),
  };
}

function paymentHistory(value: unknown): PaymentHistory | null {
  if (value === null || value === undefined) {
    return null;
  }
  const body = record(value);
  return {
    onTimePercent: whole(body["on_time_percent"]),
    onTimeAmount: whole(body["on_time_amount"]),
    dueAmount: whole(body["due_amount"]),
    longestDelayDays: whole(body["longest_delay_days"]),
  };
}

function customerDetail(value: unknown): CustomerDetail {
  const body = record(value);
  return {
    ...customer(body),
    overdue: overdue(body["overdue"]),
    paymentHistory: paymentHistory(body["payment_history"]),
    entries: list(body["entries"], entry),
    entriesTotal: whole(body["entries_total"]),
  };
}

function page<T>(item: (element: unknown) => T): (value: unknown) => Page<T> {
  return (value) => {
    const body = record(value);
    return { items: list(body["items"], item), nextCursor: textOrNull(body["next_cursor"]) };
  };
}

function overview(value: unknown): Overview {
  const body = record(value);
  const late = record(body["overdue"]);
  return {
    outstanding: whole(body["outstanding"]),
    debtors: whole(body["debtors"]),
    overdueAmount: whole(late["amount"]),
    overdueCustomers: whole(late["customers"]),
    dueToday: whole(body["due_today"]),
  };
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
    },
    customer: customer(body["customer"]),
    limitWarning: limitFigures(body["limit_warning"]),
  };
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
  };
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
  return { channel: text(body["channel"]), amount: whole(body["amount"]) };
}

function unreachableCustomer(value: unknown): UnreachableCustomer {
  const body = record(value);
  return {
    customerId: text(body["customer_id"]),
    displayName: text(body["display_name"]),
    phone: textOrNull(body["phone"]),
    amount: whole(body["amount"]),
  };
}

function subscription(value: unknown): Subscription {
  const body = record(value);
  return {
    state: text(body["state"]),
    endsOn: textOrNull(body["ends_on"]),
    daysLeft: wholeOrNull(body["days_left"]),
    priceUzs: whole(body["price_uzs"]),
    cardNumber: textOrNull(body["card_number"]),
  };
}

function addedLines(value: unknown): AddedLines {
  const made = record(record(value)["entry"]);
  return { id: text(made["id"]), amount: whole(made["amount"]), lines: list(made["lines"], goodsLine) };
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
  const body = record(value);
  return {
    id: text(body["id"]),
    name: text(body["name"]),
    lang: text(body["lang"]),
    defaultPromiseDays: whole(body["default_promise_days"]),
  };
}

/** The request form of goods lines. Refuses, before anything is sent, a line the server would refuse. */
function linesBody(lines: readonly NewLine[]): Json[] {
  if (lines.length < 1 || lines.length > MAX_LINES) {
    throw new RangeError(`an entry takes 1 to ${MAX_LINES} goods lines`);
  }
  return lines.map((line) => {
    if (lineTotal(line.qty, line.unitPrice) === null) {
      throw new RangeError("a goods line needs a quantity in thousandths and a whole price in UZS");
    }
    const body: Json = { qty: qtyToApi(line.qty), unit_price: line.unitPrice };
    if ("catalogItemId" in line) {
      body["catalog_item_id"] = line.catalogItemId;
    } else {
      body["name"] = line.name;
      if (line.unit !== null) {
        body["unit"] = line.unit;
      }
    }
    return body;
  });
}

function myShops(value: unknown): MyShops {
  const body = record(value);
  return {
    items: list(body["items"], (element) => {
      const shop = record(element);
      const role = shop["role"];
      if (!isRole(role)) {
        throw new Malformed();
      }
      return {
        shopId: text(shop["shop_id"]),
        name: text(shop["name"]),
        role,
        membershipId: textOrNull(shop["membership_id"]),
      };
    }),
    activeShop: textOrNull(body["active_shop"]),
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
  };
}

function accountDetail(value: unknown): AccountDetail {
  const body = record(value);
  const late = record(body["overdue"]);
  return {
    ...myAccount(body),
    overdueAmount: whole(late["amount"]),
    dueToday: whole(late["due_today"]),
    removalRequested: flag(body["removal_requested"]),
    entries: list(body["entries"], accountEntry),
    entriesTotal: whole(body["entries_total"]),
  };
}

function removalOutcome(value: unknown): RemovalOutcome {
  const body = record(value);
  return { removed: flag(body["removed"]), waitingForBalance: wholeOrNull(body["waiting_for_balance"]) };
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

async function errorFrom(response: Response): Promise<ApiError> {
  try {
    const failure = record(record(await response.json())["error"]);
    const fields: Record<string, string> = {};
    for (const [name, message] of Object.entries(record(failure["fields"] ?? {}))) {
      fields[name] = String(message);
    }
    return new ApiError(response.status, text(failure["code"]), textOrNull(failure["message"]), fields);
  } catch {
    // A proxy's HTML error page, for example: there is no message worth showing.
    return new ApiError(response.status, "ERROR", null);
  }
}

/** The response readers, for modules that add calls of their own (see `call`). */
export const reading = { record, text, textOrNull, whole, wholeOrNull, flag, list, page, items };

// --- requests ----------------------------------------------------------------------------------------

const IDEMPOTENCY_KEY = /^[A-Za-z0-9_-]{8,128}$/;

export type Call<T> = {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  query?: Readonly<Record<string, string | null | undefined>>;
  body?: unknown;
  idempotencyKey?: string;
  signal?: AbortSignal | undefined;
  read: (value: unknown) => T;
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
  if (request.body !== undefined) {
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
    return request.read(response.status === 204 ? null : await response.json());
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
      const body: Json = { display_name: input.displayName };
      if (input.phone !== null) {
        body["phone"] = input.phone;
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
      const body: Json = {};
      if (patch.displayName !== undefined) {
        body["display_name"] = patch.displayName;
      }
      if (patch.phone !== undefined) {
        body["phone"] = patch.phone; // null removes the number
      }
      if (patch.remindersOff !== undefined) {
        body["reminders_off"] = patch.remindersOff;
      }
      if (patch.creditLimit !== undefined) {
        if (patch.creditLimit !== null && !Number.isSafeInteger(patch.creditLimit)) {
          throw new RangeError("a credit limit must be a whole number of UZS");
        }
        body["credit_limit"] = patch.creditLimit; // null removes the customer's own limit
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
      const body: Json = { kind: input.kind };
      if (input.lines !== undefined) {
        if (input.kind !== "credit") {
          throw new RangeError("only a credit sale has goods lines");
        }
        body["lines"] = linesBody(input.lines);
      } else if (Number.isSafeInteger(input.amount)) {
        body["amount"] = input.amount;
      } else {
        throw new RangeError("amount must be a whole number of UZS");
      }
      if (input.note !== null) {
        body["note"] = input.note;
      }
      if (input.promisedDate !== null) {
        body["promised_date"] = input.promisedDate;
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
        body: { lines: linesBody(lines) },
        idempotencyKey,
        read: addedLines,
      });
    },

    /** The one-tap promised date right after a sale that was saved with the shop's usual term (REQ-008). */
    choosePromise(entryId: string, promisedDate: string, idempotencyKey: string): Promise<ChosenPromise> {
      return call(transport, {
        method: "POST",
        path: `${base}/entries/${segment(entryId)}/promise-choice`,
        body: { promised_date: promisedDate },
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
      const body: Json = { promised_date: promisedDate };
      if (reason !== null) {
        body["reason"] = reason;
      }
      return call(transport, {
        method: "POST",
        path: `${base}/entries/${segment(entryId)}/promise`,
        body,
        idempotencyKey,
        read: changedPromise,
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

    debtors(
      params: { overdue: boolean; cursor?: string | null; limit?: number },
      signal?: AbortSignal,
    ): Promise<Page<Debtor>> {
      return call(transport, {
        method: "GET",
        path: `${base}/overview/debtors`,
        query: { overdue: params.overdue ? "true" : "false", cursor: params.cursor, limit: params.limit?.toString() },
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
