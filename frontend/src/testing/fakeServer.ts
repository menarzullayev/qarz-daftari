import type { Fetch } from "../shared/api";

/** One request as the application sent it. */
export type Sent = {
  method: string;
  path: string;
  query: Record<string, string>;
  headers: Record<string, string>;
  body: unknown;
};

/** What the fake server answers: a status with a JSON body, or "offline" for a request that never arrives. */
export type Reply = { status: number; body: unknown } | "offline" | Promise<{ status: number; body: unknown } | "offline">;

export const ok = (body: unknown, status = 200) => ({ status, body });

/** The API's one error shape (interface/errors.py). */
export const refusal = (status: number, code: string, message: string, fields: Record<string, string> = {}) => ({
  status,
  body: { error: { code, message, fields } },
});

/** A `fetch` that records what was sent and answers from `handler`. Nothing leaves the test. */
export function fakeServer(handler: (sent: Sent, index: number) => Reply) {
  const sent: Sent[] = [];
  const fetch: Fetch = async (input, init) => {
    const url = new URL(input, "https://qarz.test");
    const request: Sent = {
      method: init.method ?? "GET",
      path: url.pathname,
      query: Object.fromEntries(url.searchParams),
      headers: { ...(init.headers as Record<string, string>) },
      body: typeof init.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
    };
    sent.push(request);
    const reply = await handler(request, sent.length - 1);
    if (init.signal?.aborted) {
      throw new DOMException("aborted", "AbortError");
    }
    if (reply === "offline") {
      throw new TypeError("Failed to fetch");
    }
    return new Response(JSON.stringify(reply.body), {
      status: reply.status,
      headers: { "Content-Type": "application/json" },
    });
  };
  const writes = () => sent.filter((request) => request.method !== "GET");
  return { fetch, sent, writes };
}

export const SHOP_ID = "5a0c6d3e-0000-4000-8000-00000000aaaa";
export const SHOP_BASE = `/api/v1/shops/${SHOP_ID}`;

/** Tuesday 6 October 2026, 12:00 in Tashkent. */
export const NOON = new Date("2026-10-06T07:00:00Z");

/** A promise the test settles when it chooses, to hold a request in flight. */
export function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

export function customerBody(overrides: Record<string, unknown> = {}) {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    display_name: "Ali Valiyev",
    phone: "+998901234567",
    status: "active",
    reminders_off: false,
    balance: 120000,
    ...overrides,
  };
}

export const NO_OVERDUE = { amount: 0, since: null, days: 0, due_today: 0 };

export const CUSTOMER_ID = "11111111-1111-4111-8111-111111111111";

/** The membership that recorded `entryBody()`. */
export const MEMBERSHIP_ID = "33333333-3333-4333-8333-333333333333";

export function entryBody(overrides: Record<string, unknown> = {}) {
  return {
    id: "22222222-2222-4222-8222-222222222222",
    seq: 1,
    kind: "credit",
    amount: 45000,
    note: null,
    created_at: "2026-10-05T19:30:00+00:00",
    promised_date: "2026-11-05",
    reverses_id: null,
    reversed: false,
    disputed: false,
    author_id: MEMBERSHIP_ID,
    lines: [],
    ...overrides,
  };
}

/** GET /customers/{id}: the customer with overdue status, payment history and entries. */
export function detailBody(overrides: Record<string, unknown> = {}) {
  return {
    ...customerBody(),
    overdue: NO_OVERDUE,
    payment_history: null,
    entries: [entryBody()],
    entries_total: 1,
    ...overrides,
  };
}

export const ITEM_ID = "44444444-4444-4444-8444-444444444441";

/** One catalog item as the catalog API writes it. */
export function itemBody(overrides: Record<string, unknown> = {}) {
  return {
    id: ITEM_ID,
    name: "Non",
    unit: "dona",
    price: 4000,
    learned: false,
    status: "active",
    merged_into: null,
    ...overrides,
  };
}

/** Five goods of a small shop, in the order the catalog lists them. */
export const FIVE_ITEMS = [
  itemBody({ id: "44444444-4444-4444-8444-444444444441", name: "Non", unit: "dona", price: 4000 }),
  itemBody({ id: "44444444-4444-4444-8444-444444444442", name: "Sut", unit: "l", price: 12000 }),
  itemBody({ id: "44444444-4444-4444-8444-444444444443", name: "Shakar", unit: "kg", price: 14000 }),
  itemBody({ id: "44444444-4444-4444-8444-444444444444", name: "Guruch", unit: "kg", price: 25000 }),
  itemBody({ id: "44444444-4444-4444-8444-444444444445", name: "Tuxum", unit: "dona", price: 1500 }),
];

/** One saved goods line as the ledger API writes it; the quantity is a decimal string. */
export function lineBody(overrides: Record<string, unknown> = {}) {
  return {
    line_no: 1,
    catalog_item_id: ITEM_ID,
    name: "Non",
    qty: "2",
    unit: "dona",
    unit_price: 4000,
    line_total: 8000,
    ...overrides,
  };
}

/** GET /shops/{id}: the shop's own settings. */
export function settingsBody(overrides: Record<string, unknown> = {}) {
  return { id: SHOP_ID, name: "Baraka savdo", lang: "uz", default_promise_days: 30, ...overrides };
}

/** GET /customers/{id}/link for a customer nobody has connected yet. */
export function linkBody(overrides: Record<string, unknown> = {}) {
  return { linked: false, status: null, since: null, ...overrides };
}

/** A start code as the server issues one: a prefix and 43 URL-safe characters. Not a real credential. */
export const START = "c_Zm9yLXRlc3RzLW9ubHktbm90LWEtcmVhbC10b2tlbi0wMDAw";
export const COUNTER_START = "k_Zm9yLXRlc3RzLW9ubHktbm90LWEtcmVhbC10b2tlbi0xMTEx";

export const LINK_ID = "77777777-7777-4777-8777-777777777777";
export const ME_BASE = `/api/v1/me/accounts/${LINK_ID}`;
export const DISPUTE_ID = "88888888-8888-4888-8888-888888888888";

/** A dispute as it appears on the customer's own entry. */
export function disputeBody(overrides: Record<string, unknown> = {}) {
  return { id: DISPUTE_ID, status: "open", reason: "Men bu tovarni olmaganman", decline_reason: null, ...overrides };
}

/** An entry of the customer's own account: no note, no author and no sequence number. */
export function accountEntryBody(overrides: Record<string, unknown> = {}) {
  return {
    id: "22222222-2222-4222-8222-222222222222",
    kind: "credit",
    amount: 45000,
    created_at: "2026-10-05T19:30:00+00:00",
    promised_date: "2026-11-05",
    reverses_id: null,
    reversed: false,
    disputed: false,
    dispute: null,
    lines: [],
    ...overrides,
  };
}

/** GET /me/accounts/{link_id}. */
export function accountBody(overrides: Record<string, unknown> = {}) {
  return {
    link_id: LINK_ID,
    shop_name: "Baraka savdo",
    display_name: "Ali Valiyev",
    balance: 120000,
    overdue: { amount: 0, due_today: 0 },
    removal_requested: false,
    entries: [accountEntryBody()],
    entries_total: 1,
    ...overrides,
  };
}

/** One row of GET /shops/{id}/disputes. */
export function openDisputeBody(overrides: Record<string, unknown> = {}) {
  return {
    ...disputeBody(),
    entry_id: "22222222-2222-4222-8222-222222222222",
    created_at: "2026-10-06T05:10:00+00:00",
    customer_id: CUSTOMER_ID,
    customer_name: "Ali Valiyev",
    amount: 45000,
    ...overrides,
  };
}
