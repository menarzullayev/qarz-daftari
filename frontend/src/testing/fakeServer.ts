import type { Fetch } from "../shared/api";

/** One request as the application sent it. */
export type Sent = {
  method: string;
  path: string;
  query: Record<string, string>;
  headers: Record<string, string>;
  body: unknown;
  /** The fields of a multipart form, when the request carried one instead of JSON. */
  form?: Record<string, FormDataEntryValue>;
};

/** What the fake server answers: a status with a JSON body, or "offline" for a request that never arrives. */
export type Reply =
  | { status: number; body: unknown; headers?: Record<string, string> }
  | "offline"
  | Promise<{ status: number; body: unknown; headers?: Record<string, string> } | "offline">;

export const ok = (body: unknown, status = 200) => ({ status, body });
type Answer = { status: number; body: unknown; headers?: Record<string, string> };

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
    if (init.body instanceof FormData) {
      request.form = Object.fromEntries(init.body.entries());
    }
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
      headers: { "Content-Type": "application/json", ...(reply as Answer).headers },
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
    credit_limit: null,
    balance: 120000,
    ...overrides,
  };
}

/** GET /shops/{id}/credit-settings: no default limit, and a seller is stopped at a customer's limit. */
export function creditSettingsBody(overrides: Record<string, unknown> = {}) {
  return { default_credit_limit: null, sellers_may_exceed: false, limit_bounds: [1000, 10000000000], ...overrides };
}

/** The three fixed wordings as the server returns them (application/chat_texts.py), shortened to two forms each. */
export const REMINDER_TEMPLATES = [1, 2, 3].map((id) => ({
  id,
  due_today: {
    uz: `${id}: «{shop}»: {name}, bugun {amount} to'lash kuni.`,
    ru: `${id}: «{shop}»: {name}, сегодня срок оплаты {amount}.`,
  },
  overdue: {
    uz: `${id}: «{shop}»: {name}, {amount} qarzning to'lash muddati o'tgan.`,
    ru: `${id}: «{shop}»: {name}, срок оплаты долга {amount} прошёл.`,
  },
}));

/** GET /shops/{id}/reminders: off, at ten o'clock, the first wording, no SMS. */
export function remindersBody(overrides: Record<string, unknown> = {}) {
  return { on: false, hour: 10, template: 1, sms_on: false, hours: [8, 20], templates: REMINDER_TEMPLATES, ...overrides };
}

/** GET /shops/{id}/subscription: a trial with twenty days left, and a card to pay to. */
export function subscriptionBody(overrides: Record<string, unknown> = {}) {
  return {
    state: "trial",
    trial_ends: "2026-10-26",
    paid_through: null,
    ends_on: "2026-10-26",
    days_left: 20,
    price_uzs: 100000,
    card_number: "8600 1234 5678 9012",
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

/** One promised date in an entry's history (ledger_service.promise_body). */
export function promiseBody(overrides: Record<string, unknown> = {}) {
  return { promised_date: "2026-11-05", actor: "default", reason: null, created_at: "2026-10-05T19:30:00+00:00", ...overrides };
}

export const DATE_REQUEST_ID = "99999999-9999-4999-8999-999999999999";

/** A customer's request to move the date of `entryBody()` from 5 to 20 November, still open. */
export function dateRequestBody(overrides: Record<string, unknown> = {}) {
  return {
    id: DATE_REQUEST_ID,
    entry_id: "22222222-2222-4222-8222-222222222222",
    status: "open",
    requested_date: "2026-11-20",
    reason: "Oylik kechikdi",
    decline_reason: null,
    created_at: "2026-10-06T05:10:00+00:00",
    closed_at: null,
    ...overrides,
  };
}

/** One row of GET /shops/{id}/date-requests. */
export function openDateRequestBody(overrides: Record<string, unknown> = {}) {
  return {
    ...dateRequestBody(),
    customer_id: CUSTOMER_ID,
    customer_name: "Ali Valiyev",
    amount: 45000,
    promised_date: "2026-11-05",
    ...overrides,
  };
}

/** GET /shops/{id}/reports/period for 1 to 6 October 2026: 500 000 + 300 000 + 50 000 − 250 000 = 600 000. */
export function periodReportBody(overrides: Record<string, unknown> = {}) {
  return {
    from: "2026-10-01",
    to: "2026-10-06",
    outstanding: { start: 500000, end: 600000 },
    credit: { amount: 300000, count: 4, customers: 3 },
    payments: { amount: 250000, count: 2, customers: 2 },
    opening: { amount: 50000, count: 1 },
    net_change: 100000,
    reversals: { amount: 20000, count: 1 },
    new_customers: 2,
    disputes_opened: 1,
    on_time: { due_amount: 200000, on_time_amount: 150000, percent: 75 },
    days: [
      { date: "2026-10-01", credit: 100000, payments: 0 },
      { date: "2026-10-02", credit: 0, payments: 0 },
      { date: "2026-10-03", credit: 0, payments: 250000 },
      { date: "2026-10-04", credit: 0, payments: 0 },
      { date: "2026-10-05", credit: 0, payments: 0 },
      { date: "2026-10-06", credit: 200000, payments: 0 },
    ],
    top_debtors: [
      { customer_id: CUSTOMER_ID, display_name: "Ali Valiyev", balance: 400000 },
      { customer_id: "11111111-1111-4111-8111-111111111112", display_name: "Vali Aliyev", balance: 200000 },
    ],
    staff: [
      { membership_id: MEMBERSHIP_ID, role: "owner", credit: { amount: 100000, count: 1 }, payments: { amount: 250000, count: 2 } },
      {
        membership_id: "33333333-3333-4333-8333-3333333d4e5f",
        role: "seller",
        credit: { amount: 200000, count: 3 },
        payments: { amount: 0, count: 0 },
      },
    ],
    ...overrides,
  };
}

/** GET /shops/{id}/reports/overdue as of 6 October 2026. */
export function overdueReportBody(overrides: Record<string, unknown> = {}) {
  return {
    as_of: "2026-10-06",
    total: { amount: 180000, customers: 3 },
    bands: [
      { band: "1_7", from_days: 1, to_days: 7, amount: 45000, customers: 1 },
      { band: "8_30", from_days: 8, to_days: 30, amount: 100000, customers: 2 },
      { band: "31_90", from_days: 31, to_days: 90, amount: 0, customers: 0 },
      { band: "over_90", from_days: 91, to_days: null, amount: 35000, customers: 1 },
    ],
    ...overrides,
  };
}

export const NOTICE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

/** A payment notice of 50 000 UZS, sent on 6 October 2026 and still waiting (notice_view.notice_body). */
export function noticeBody(overrides: Record<string, unknown> = {}) {
  return {
    id: NOTICE_ID,
    status: "sent",
    amount: 50000,
    recorded_amount: null,
    payment_entry_id: null,
    has_receipt: false,
    decline_reason: null,
    created_at: "2026-10-06T05:10:00+00:00",
    closed_at: null,
    expires_at: "2026-10-20T05:10:00+00:00",
    ...overrides,
  };
}

/** One row of GET /shops/{id}/payment-notices: the notice with its customer and what they owe. */
export function openNoticeBody(overrides: Record<string, unknown> = {}) {
  return {
    ...noticeBody({ has_receipt: true }),
    receipt_seen_before: false,
    customer_id: CUSTOMER_ID,
    customer_name: "Ali Valiyev",
    customer_balance: 120000,
    ...overrides,
  };
}

export const EXPORT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

/** An export as GET /shops/{id}/exports lists one: finished an hour before `NOON`, its file kept a week. */
export function exportBody(overrides: Record<string, unknown> = {}) {
  return {
    id: EXPORT_ID,
    status: "done",
    error: null,
    requested_by: MEMBERSHIP_ID,
    created_at: "2026-10-06T05:58:00+00:00",
    finished_at: "2026-10-06T06:00:00+00:00",
    rows: 1234,
    available: true,
    available_until: "2026-10-13T06:00:00+00:00",
    ...overrides,
  };
}

export const ACCESS_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
export const SUPPORT_ADMIN_ID = "dddddddd-dddd-4ddd-8ddd-dddddda1b2c3";

/** A support access as the owner's list gives one: open at `NOON`, from an hour before it to an hour after. */
export function supportAccessBody(overrides: Record<string, unknown> = {}) {
  return {
    id: ACCESS_ID,
    shop_id: SHOP_ID,
    admin_id: SUPPORT_ADMIN_ID,
    reason: "Egasi yordam so'radi",
    state: "active",
    starts_at: "2026-10-06T06:00:00+00:00",
    ends_at: "2026-10-06T08:00:00+00:00",
    closed_at: null,
    closed_by: null,
    ...overrides,
  };
}
