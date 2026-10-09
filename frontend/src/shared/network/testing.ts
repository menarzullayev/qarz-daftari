import { SHOP_BASE } from "../../testing/fakeServer";

/** Bodies of the network's API as the server writes them, for the tests of its screens. */

export const NETWORK = `${SHOP_BASE}/network`;

export const NET_LINK_ID = "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1";
export const OTHER_LINK_ID = "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a2";
export const INVITE_ID = "b2b2b2b2-b2b2-4b2b-8b2b-b2b2b2b2b2b2";
export const ORDER_ID = "c3c3c3c3-c3c3-4c3c-8c3c-c3c3c3c3c3c3";
export const NOTE_ID = "d4d4d4d4-d4d4-4d4d-8d4d-d4d4d4d4d4d4";
export const PAYMENT_ID = "e5e5e5e5-e5e5-4e5e-8e5e-e5e5e5e5e5e5";
export const COUNTERPART_ID = "77777777-7777-4777-8777-777777777771";
export const OWN_ITEM_ID = "44444444-4444-4444-8444-444444444441";
export const STOCK_DOCUMENT_ID = "88888888-8888-4888-8888-888888888881";

/** A code as the server issues one. Not a real credential. */
export const CODE = "QD-7H2K-9XWM-41";

/** An active link in which this shop is the buyer. */
export function linkBody(overrides: Record<string, unknown> = {}) {
  return {
    id: NET_LINK_ID,
    role: "buyer",
    state: "active",
    invited: false,
    partner: { name: "Baraka ulgurji", phone: "+998901112233", removed: false },
    counterpart: { kind: "supplier", id: COUNTERPART_ID },
    requested_at: "2026-10-01T05:00:00+00:00",
    decided_at: "2026-10-01T06:00:00+00:00",
    ended_at: null,
    ended_by: null,
    ...overrides,
  };
}

export function waitingBody(overrides: Record<string, unknown> = {}) {
  return { links: 0, orders: 0, deliveries: 0, notes: 0, rejected_notes: 0, payments: 0, ...overrides };
}

export function overviewBody(overrides: Record<string, unknown> = {}) {
  return { links: [linkBody()], waiting: waitingBody(), ...overrides };
}

export function inviteBody(overrides: Record<string, unknown> = {}) {
  return { id: INVITE_ID, as: "supplier", expires_at: "2026-10-08T07:00:00+00:00", ...overrides };
}

export function eventBody(overrides: Record<string, unknown> = {}) {
  return { kind: "link_requested", by: "partner", member_id: null, at: "2026-10-01T05:00:00+00:00", detail: null, ...overrides };
}

/** One currency of a link whose two books agree and where nothing awaits. */
export function reconciliationBody(overrides: Record<string, unknown> = {}) {
  return {
    currency: "UZS",
    agreed: { delivered: 900000, paid: 400000, balance: 500000 },
    awaiting: { notes: 0, payments_own: 0, payments_partner: 0, payments_declined: 0 },
    own_balance: 500000,
    difference: 0,
    ...overrides,
  };
}

/** An order this shop was sent and has not answered. */
export function orderBody(overrides: Record<string, unknown> = {}) {
  return {
    id: ORDER_ID,
    link_id: NET_LINK_ID,
    role: "supplier",
    partner: { name: "Ziyo market" },
    number: 12,
    status: "sent",
    note: null,
    wanted_date: "2026-10-09",
    currency: null,
    total: null,
    sent_at: "2026-10-06T05:00:00+00:00",
    updated_at: "2026-10-06T05:00:00+00:00",
    closed_reason: null,
    lines: [
      { line_no: 1, name: "Shakar", unit: "kg", qty: "50", item_id: null, accepted_qty: null, unit_price: null, line_total: null, changed: false },
      { line_no: 2, name: "Choy", unit: "dona", qty: "10", item_id: null, accepted_qty: null, unit_price: null, line_total: null, changed: false },
    ],
    delivery_note: null,
    events: [eventBody({ kind: "order_sent" })],
    ...overrides,
  };
}

/** A delivery note this shop was issued and has not answered: one line, partly paid on delivery. */
export function noteBody(overrides: Record<string, unknown> = {}) {
  return {
    id: NOTE_ID,
    link_id: NET_LINK_ID,
    order_id: ORDER_ID,
    order_number: 12,
    role: "buyer",
    partner: { name: "Baraka ulgurji" },
    number: 5,
    status: "issued",
    currency: "UZS",
    total: 600000,
    paid: 0,
    terms: "credit",
    issued_at: "2026-10-06T06:00:00+00:00",
    decided_at: null,
    reject_reason: null,
    supersedes_id: null,
    supersede_reason: null,
    lines: [{ line_no: 1, name: "Shakar", unit: "kg", qty: "50", unit_price: 12000, line_total: 600000, item_id: OWN_ITEM_ID, received_qty: null }],
    events: [eventBody({ kind: "note_issued" })],
    ...overrides,
  };
}

/** A payment the partner recorded, which awaits this shop. */
export function paymentBody(overrides: Record<string, unknown> = {}) {
  return {
    id: PAYMENT_ID,
    link_id: NET_LINK_ID,
    role: "supplier",
    partner: { name: "Ziyo market" },
    recorded_by: "partner",
    status: "awaiting",
    amount: 250000,
    currency: "UZS",
    note: null,
    recorded_at: "2026-10-06T06:30:00+00:00",
    decided_at: null,
    decline_reason: null,
    in_own_books: false,
    ...overrides,
  };
}

export function draftBody(overrides: Record<string, unknown> = {}) {
  return {
    id: ORDER_ID,
    status: "draft",
    link_id: NET_LINK_ID,
    partner: { name: "Baraka ulgurji" },
    note: null,
    wanted_date: null,
    lines: [{ line_no: 1, name: "Shakar", unit: "kg", qty: "50", item_id: null }],
    updated_at: "2026-10-06T05:00:00+00:00",
    ...overrides,
  };
}
