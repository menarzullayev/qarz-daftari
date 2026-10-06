import { isRole, type Role } from "./navigation";

/**
 * Client for the staff API (/api/v1). The server is the authority: it checks the role, the amounts and
 * the dates on every call. This module only shapes requests and refuses a response that does not look
 * like the contract, so a screen never shows a balance that is not a whole number of UZS.
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
export type NewEntry = { kind: EntryKind; amount: number; note: string | null; promisedDate: string | null };
export type RecordedEntry = {
  entry: { id: string; kind: string; amount: number; promisedDate: string | null };
  /** The customer after the entry; `balance` is the new balance. */
  customer: Customer;
};

export type CustomerPatch = { displayName?: string; phone?: string | null; remindersOff?: boolean };

export type ShopMembership = { shopId: string; name: string; role: Role };
export type MyShops = { items: ShopMembership[]; activeShop: string | null };

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
    },
    customer: customer(body["customer"]),
  };
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
      return { shopId: text(shop["shop_id"]), name: text(shop["name"]), role };
    }),
    activeShop: textOrNull(body["active_shop"]),
  };
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

// --- requests ----------------------------------------------------------------------------------------

const IDEMPOTENCY_KEY = /^[A-Za-z0-9_-]{8,128}$/;

type Call<T> = {
  method: "GET" | "POST" | "PUT" | "PATCH";
  path: string;
  query?: Readonly<Record<string, string | null | undefined>>;
  body?: unknown;
  idempotencyKey?: string;
  signal?: AbortSignal | undefined;
  read: (value: unknown) => T;
};

type Transport = { fetch: Fetch; auth: ApiAuth | null; onUnauthenticated?: (() => void) | undefined };

async function call<T>(transport: Transport, request: Call<T>): Promise<T> {
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
      if (!Number.isSafeInteger(input.amount)) {
        throw new RangeError("amount must be a whole number of UZS");
      }
      const body: Json = { kind: input.kind, amount: input.amount };
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

export function createApi(transport: { fetch: Fetch; auth: ApiAuth; onUnauthenticated?: () => void }) {
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
