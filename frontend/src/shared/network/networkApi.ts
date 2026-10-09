import { reading, type ShopApi } from "../api";
import type { Currency } from "../money";

/**
 * The network between shops (the expansion's module J): two shops of the platform linked as buyer and
 * supplier, their orders, delivery notes and payments. Built on the shop API's `send` in a module that
 * is loaded apart, so none of it is part of the first load. Shapes follow
 * backend/src/qarz/application/network.py, network_orders.py and network_payments.py.
 *
 * Quantities are the API's decimal strings (up to three decimals) and stay strings; money is a whole
 * number of the currency's minor unit with its currency beside it, and two currencies are never added.
 * `role` is always what THIS shop is in the link; `by` and `recordedBy` say "own" for this shop and
 * "partner" for the other: nothing of the partner's members is ever named.
 */

const { record, text, textOrNull, whole, wholeOrNull, flag, list } = reading;

const QTY = /^\d{1,12}(?:\.\d{1,3})?$/;

function qty(value: unknown): string {
  const raw = text(value);
  if (!QTY.test(raw)) {
    throw new RangeError("not a quantity");
  }
  return raw;
}

function qtyOrNull(value: unknown): string | null {
  return value === null || value === undefined ? null : qty(value);
}

function oneOf<T extends string>(known: readonly T[], value: unknown): T {
  const found = known.find((candidate) => candidate === value);
  if (found === undefined) {
    throw new RangeError("not a value this client knows");
  }
  return found;
}

function oneOfOrNull<T extends string>(known: readonly T[], value: unknown): T | null {
  return value === null || value === undefined ? null : oneOf(known, value);
}

const CURRENCY_CODES: readonly Currency[] = ["UZS", "USD"];
const currency = (value: unknown): Currency => oneOf(CURRENCY_CODES, value);
const currencyOrNull = (value: unknown): Currency | null => oneOfOrNull(CURRENCY_CODES, value);

export const LINK_ROLES = ["buyer", "supplier"] as const;
export type LinkRole = (typeof LINK_ROLES)[number];

export const LINK_STATES = ["requested", "active", "declined", "ended"] as const;
export type LinkState = (typeof LINK_STATES)[number];

const SIDES = ["own", "partner"] as const;
export type Side = (typeof SIDES)[number];

/** The other shop as this one may know it. `removed`: the shop is gone, and no name is shown for it. */
export type Partner = { name: string | null; phone: string | null; removed: boolean };

/** The row of this shop's own books the link stands for: a supplier of the buyer, a customer of the supplier. */
export type Counterpart = { kind: "supplier" | "customer"; id: string };

export type NetLink = {
  id: string;
  role: LinkRole;
  state: LinkState;
  /** This shop made the invitation, and so it is the one that answers the request. */
  invited: boolean;
  partner: Partner;
  counterpart: Counterpart | null;
  requestedAt: string;
  decidedAt: string | null;
  endedAt: string | null;
  endedBy: Side | null;
};

function partner(value: unknown): Partner {
  const body = record(value);
  return { name: textOrNull(body["name"]), phone: textOrNull(body["phone"]), removed: body["removed"] === true };
}

function netLink(value: unknown): NetLink {
  const body = record(value);
  const counterpart = body["counterpart"];
  return {
    id: text(body["id"]),
    role: oneOf(LINK_ROLES, body["role"]),
    state: oneOf(LINK_STATES, body["state"]),
    invited: flag(body["invited"]),
    partner: partner(body["partner"]),
    counterpart:
      counterpart === null || counterpart === undefined
        ? null
        : { kind: oneOf(["supplier", "customer"] as const, record(counterpart)["kind"]), id: text(record(counterpart)["id"]) },
    requestedAt: text(body["requested_at"]),
    decidedAt: textOrNull(body["decided_at"]),
    endedAt: textOrNull(body["ended_at"]),
    endedBy: oneOfOrNull(SIDES, body["ended_by"]),
  };
}

/** One step of a link's, an order's, a note's or a payment's history. A kind this client does not know is kept as it came. */
export type NetEvent = { kind: string; by: Side; at: string; detail: Readonly<Record<string, unknown>> | null };

function netEvent(value: unknown): NetEvent {
  const body = record(value);
  const detail = body["detail"];
  return {
    kind: text(body["kind"]),
    by: oneOf(SIDES, body["by"]),
    at: text(body["at"]),
    detail: detail === null || detail === undefined ? null : record(detail),
  };
}

const events = (value: unknown): NetEvent[] => (value === undefined || value === null ? [] : list(value, netEvent));

/** What awaits THIS shop's step, as counts. */
export type Waiting = {
  /** Requests to link, to answer. */
  links: number;
  /** Incoming orders, to accept or decline. */
  orders: number;
  /** Accepted orders, to deliver. */
  deliveries: number;
  /** Delivery notes, to confirm or reject. */
  notes: number;
  /** Notes the buyer rejected, to correct. */
  rejectedNotes: number;
  /** Payments the partner recorded, to answer. */
  payments: number;
};

export const NO_WAITING: Waiting = { links: 0, orders: 0, deliveries: 0, notes: 0, rejectedNotes: 0, payments: 0 };

function waiting(value: unknown): Waiting {
  const body = record(value);
  return {
    links: whole(body["links"]),
    orders: whole(body["orders"]),
    deliveries: whole(body["deliveries"]),
    notes: whole(body["notes"]),
    rejectedNotes: whole(body["rejected_notes"]),
    payments: whole(body["payments"]),
  };
}

/** An invitation that stands: `as` is what THIS shop will be. Its code is never sent again. */
export type Invite = { id: string; as: LinkRole; expiresAt: string };
/** The same at the one moment its code is known: when it is made. */
export type IssuedInvite = Invite & { code: string };

function invite(value: unknown): Invite {
  const body = record(value);
  return { id: text(body["id"]), as: oneOf(LINK_ROLES, body["as"]), expiresAt: text(body["expires_at"]) };
}

export type Overview = {
  links: NetLink[];
  waiting: Waiting;
  /** Present only for a member who may manage the links. */
  invites?: Invite[];
};

function overview(value: unknown): Overview {
  const body = record(value);
  const result: Overview = { links: list(body["links"], netLink), waiting: waiting(body["waiting"]) };
  if (body["invites"] !== undefined && body["invites"] !== null) {
    result.invites = list(body["invites"], invite);
  }
  return result;
}

/**
 * The two books of one link in one currency. `agreed` is what both shops confirmed through the network;
 * `awaiting` is money that one side knows of and the other has not confirmed; `ownBalance` is this
 * shop's own account of the partner and `difference` is that less the agreed balance. The last two are
 * absent, never zero, for a member who may not see that account.
 */
export type Reconciliation = {
  currency: Currency;
  agreed: { delivered: number; paid: number; balance: number };
  awaiting: {
    /** Delivered and not yet confirmed: in nobody's books. */
    notes: number;
    /** Payments this shop recorded and the partner has not confirmed: in this shop's books only. */
    paymentsOwn: number;
    /** Payments the partner recorded that await this shop: not in this shop's books. */
    paymentsPartner: number;
    /** Payments the partner declined that still stand in this shop's books. */
    paymentsDeclined: number;
  };
  ownBalance?: number;
  difference?: number;
};

function reconciliation(value: unknown): Reconciliation {
  const body = record(value);
  const agreed = record(body["agreed"]);
  const awaiting = record(body["awaiting"]);
  const row: Reconciliation = {
    currency: currency(body["currency"]),
    agreed: { delivered: whole(agreed["delivered"]), paid: whole(agreed["paid"]), balance: whole(agreed["balance"]) },
    awaiting: {
      notes: whole(awaiting["notes"]),
      paymentsOwn: whole(awaiting["payments_own"]),
      paymentsPartner: whole(awaiting["payments_partner"]),
      paymentsDeclined: whole(awaiting["payments_declined"]),
    },
  };
  if (body["own_balance"] !== undefined && body["own_balance"] !== null) {
    row.ownBalance = whole(body["own_balance"]);
    row.difference = whole(body["difference"]);
  }
  return row;
}

export type LinkPage = { link: NetLink; reconciliation: Reconciliation[]; events: NetEvent[] };

function linkPage(value: unknown): LinkPage {
  const body = record(value);
  return { link: netLink(body["link"]), reconciliation: list(body["reconciliation"], reconciliation), events: events(body["events"]) };
}

export const ORDER_STATUSES = ["sent", "accepted", "delivered", "received", "declined", "cancelled"] as const;
export type OrderStatus = (typeof ORDER_STATUSES)[number];

const partnerName = (value: unknown): string | null => textOrNull(record(value)["name"]);

export type OrderSummary = {
  id: string;
  linkId: string;
  role: LinkRole;
  partnerName: string | null;
  number: number;
  status: OrderStatus;
  note: string | null;
  wantedDate: string | null;
  currency: Currency | null;
  total: number | null;
  sentAt: string;
  updatedAt: string;
  closedReason: string | null;
};

export type OrderLine = {
  lineNo: number;
  name: string;
  unit: string;
  qty: string;
  /** This shop's own catalogue item for the line, when it chose one. */
  itemId: string | null;
  acceptedQty: string | null;
  unitPrice: number | null;
  lineTotal: number | null;
  /** The supplier accepted another quantity than was asked. */
  changed: boolean;
};

export type Order = OrderSummary & { lines: OrderLine[]; deliveryNote: NoteSummary | null; events: NetEvent[] };

function orderSummary(value: unknown): OrderSummary {
  const body = record(value);
  return {
    id: text(body["id"]),
    linkId: text(body["link_id"]),
    role: oneOf(LINK_ROLES, body["role"]),
    partnerName: partnerName(body["partner"]),
    number: whole(body["number"]),
    status: oneOf(ORDER_STATUSES, body["status"]),
    note: textOrNull(body["note"]),
    wantedDate: textOrNull(body["wanted_date"]),
    currency: currencyOrNull(body["currency"]),
    total: wholeOrNull(body["total"]),
    sentAt: text(body["sent_at"]),
    updatedAt: text(body["updated_at"]),
    closedReason: textOrNull(body["closed_reason"]),
  };
}

function orderLine(value: unknown): OrderLine {
  const body = record(value);
  return {
    lineNo: whole(body["line_no"]),
    name: text(body["name"]),
    unit: text(body["unit"]),
    qty: qty(body["qty"]),
    itemId: textOrNull(body["item_id"]),
    acceptedQty: qtyOrNull(body["accepted_qty"]),
    unitPrice: wholeOrNull(body["unit_price"]),
    lineTotal: wholeOrNull(body["line_total"]),
    changed: body["changed"] === true,
  };
}

function order(value: unknown): Order {
  const body = record(value);
  const note = body["delivery_note"];
  return {
    ...orderSummary(value),
    lines: list(body["lines"], orderLine),
    deliveryNote: note === null || note === undefined ? null : noteSummary(note),
    events: events(body["events"]),
  };
}

export type DraftLine = { lineNo: number; name: string; unit: string; qty: string; itemId: string | null };

/** An order the buyer is still writing: nothing of it has reached the supplier. */
export type Draft = {
  id: string;
  linkId: string;
  partnerName: string | null;
  note: string | null;
  wantedDate: string | null;
  lines: DraftLine[];
  updatedAt: string;
};

function draft(value: unknown): Draft {
  const body = record(value);
  return {
    id: text(body["id"]),
    linkId: text(body["link_id"]),
    partnerName: partnerName(body["partner"]),
    note: textOrNull(body["note"]),
    wantedDate: textOrNull(body["wanted_date"]),
    lines: list(body["lines"], (element) => {
      const line = record(element);
      return {
        lineNo: whole(line["line_no"]),
        name: text(line["name"]),
        unit: text(line["unit"]),
        qty: qty(line["qty"]),
        itemId: textOrNull(line["item_id"]),
      };
    }),
    updatedAt: text(body["updated_at"]),
  };
}

/** What a draft says, to save. A key that does not apply is left out: the server refuses extras. */
export type DraftInput = {
  linkId: string;
  note: string | null;
  /** A calendar day, YYYY-MM-DD. */
  wantedDate: string | null;
  lines: readonly { name: string; unit: string; qty: string; itemId: string | null }[];
};

export function draftBody(input: DraftInput): Record<string, unknown> {
  const body: Record<string, unknown> = {
    link_id: input.linkId,
    lines: input.lines.map((line) => {
      const row: Record<string, unknown> = { name: line.name, unit: line.unit, qty: line.qty };
      if (line.itemId !== null) {
        row["item_id"] = line.itemId;
      }
      return row;
    }),
  };
  if (input.note !== null && input.note !== "") {
    body["note"] = input.note;
  }
  if (input.wantedDate !== null && input.wantedDate !== "") {
    body["wanted_date"] = input.wantedDate;
  }
  return body;
}

export const NOTE_STATUSES = ["issued", "received", "rejected", "superseded", "void"] as const;
export type NoteStatus = (typeof NOTE_STATUSES)[number];

export const NOTE_TERMS = ["paid", "credit", "part"] as const;
export type NoteTerms = (typeof NOTE_TERMS)[number];

export type NoteSummary = {
  id: string;
  linkId: string;
  orderId: string;
  orderNumber: number;
  role: LinkRole;
  partnerName: string | null;
  number: number;
  status: NoteStatus;
  currency: Currency;
  total: number;
  /** Handed over on delivery; the rest of the total is on credit. */
  paid: number;
  terms: NoteTerms;
  issuedAt: string;
  decidedAt: string | null;
  rejectReason: string | null;
  supersedesId: string | null;
  supersedeReason: string | null;
};

export type NoteLine = {
  /** The number of the ORDER's line the goods answer. */
  lineNo: number;
  name: string;
  unit: string;
  qty: string;
  unitPrice: number;
  lineTotal: number;
  /** This shop's own catalogue item for the line, when one is known. */
  itemId: string | null;
  /** What the buyer said it received, on a note it rejected. */
  receivedQty: string | null;
};

export type Note = NoteSummary & {
  lines: NoteLine[];
  events: NetEvent[];
  /** The buyer's side, once confirmed: the stock receipt the note became. */
  stockDocumentId?: string;
  /** The supplier's side, once confirmed: the customer whose account carries the sale. */
  customerId?: string;
  ledgerEntryId?: string;
};

function noteSummary(value: unknown): NoteSummary {
  const body = record(value);
  return {
    id: text(body["id"]),
    linkId: text(body["link_id"]),
    orderId: text(body["order_id"]),
    orderNumber: whole(body["order_number"]),
    role: oneOf(LINK_ROLES, body["role"]),
    partnerName: partnerName(body["partner"]),
    number: whole(body["number"]),
    status: oneOf(NOTE_STATUSES, body["status"]),
    currency: currency(body["currency"]),
    total: whole(body["total"]),
    paid: whole(body["paid"]),
    terms: oneOf(NOTE_TERMS, body["terms"]),
    issuedAt: text(body["issued_at"]),
    decidedAt: textOrNull(body["decided_at"]),
    rejectReason: textOrNull(body["reject_reason"]),
    supersedesId: textOrNull(body["supersedes_id"]),
    supersedeReason: textOrNull(body["supersede_reason"]),
  };
}

function note(value: unknown): Note {
  const body = record(value);
  const result: Note = {
    ...noteSummary(value),
    lines: list(body["lines"], (element) => {
      const line = record(element);
      return {
        lineNo: whole(line["line_no"]),
        name: text(line["name"]),
        unit: text(line["unit"]),
        qty: qty(line["qty"]),
        unitPrice: whole(line["unit_price"]),
        lineTotal: whole(line["line_total"]),
        itemId: textOrNull(line["item_id"]),
        receivedQty: qtyOrNull(line["received_qty"]),
      };
    }),
    events: events(body["events"]),
  };
  const optional = [
    ["stockDocumentId", "stock_document_id"],
    ["customerId", "customer_id"],
    ["ledgerEntryId", "ledger_entry_id"],
  ] as const;
  for (const [name, key] of optional) {
    const found = textOrNull(body[key]);
    if (found !== null) {
      result[name] = found;
    }
  }
  return result;
}

export const PAYMENT_STATUSES = ["awaiting", "confirmed", "declined", "withdrawn", "lapsed"] as const;
export type PaymentStatus = (typeof PAYMENT_STATUSES)[number];

/** How money was paid, for the cash book of the shop that says it. */
export const METHODS = ["cash", "card", "transfer"] as const;
export type Method = (typeof METHODS)[number];

/** Money the buyer paid the supplier, as one of them recorded it; the other side confirms or declines. */
export type Payment = {
  id: string;
  linkId: string;
  role: LinkRole;
  partnerName: string | null;
  recordedBy: Side;
  status: PaymentStatus;
  amount: number;
  currency: Currency;
  note: string | null;
  recordedAt: string;
  decidedAt: string | null;
  declineReason: string | null;
  /** The payment stands in this shop's own books right now. */
  inOwnBooks: boolean;
  events: NetEvent[];
};

function payment(value: unknown): Payment {
  const body = record(value);
  return {
    id: text(body["id"]),
    linkId: text(body["link_id"]),
    role: oneOf(LINK_ROLES, body["role"]),
    partnerName: partnerName(body["partner"]),
    recordedBy: oneOf(SIDES, body["recorded_by"]),
    status: oneOf(PAYMENT_STATUSES, body["status"]),
    amount: whole(body["amount"]),
    currency: currency(body["currency"]),
    note: textOrNull(body["note"]),
    recordedAt: text(body["recorded_at"]),
    decidedAt: textOrNull(body["decided_at"]),
    declineReason: textOrNull(body["decline_reason"]),
    inOwnBooks: body["in_own_books"] === true,
    events: events(body["events"]),
  };
}

/** The supplier's answer to one line of an order: every line is answered, and "0" is an answer. */
export type AcceptLine = { lineNo: number; qty: string; unitPrice: number; itemId: string | null };

/**
 * Where a line of a delivery note goes in the buyer's catalogue: onto one of its own items of the same
 * unit, or as a new item with the price the buyer will sell it for.
 */
export type ConfirmLine = { lineNo: number } & ({ itemId: string } | { newPrice: number; newBarcode: string | null });

export type Page<T> = { items: T[]; nextCursor: string | null };

function paged<T>(key: string, item: (element: unknown) => T): (value: unknown) => Page<T> {
  return (value) => {
    const body = record(value);
    return { items: list(body[key], item), nextCursor: textOrNull(body["next_cursor"]) };
  };
}

export type ListParams = {
  role?: LinkRole | undefined;
  status?: string | undefined;
  /** One link's only. */
  link?: string | undefined;
  cursor?: string | null | undefined;
  limit?: number | undefined;
};

function listQuery(params: ListParams): Record<string, string | null | undefined> {
  return { role: params.role, status: params.status, link: params.link, cursor: params.cursor, limit: params.limit?.toString() };
}

const id = encodeURIComponent;

export function networkOf(api: ShopApi) {
  const base = `${api.base}/network`;
  /** A write that says nothing but "do it": the path and the key. */
  const act = <T>(path: string, idempotencyKey: string, read: (value: unknown) => T, body?: Record<string, unknown>) =>
    api.send({ method: "POST", path: `${base}${path}`, body, idempotencyKey, read });
  const linkOf = (value: unknown) => netLink(record(value)["link"]);
  return {
    overview(signal?: AbortSignal): Promise<Overview> {
      return api.send({ method: "GET", path: base, signal, read: overview });
    },

    /** Makes an invitation; `as` is what THIS shop will be. The code in the answer is shown once. */
    createInvite(as: LinkRole, idempotencyKey: string): Promise<IssuedInvite> {
      return act("/invites", idempotencyKey, (value) => ({ ...invite(value), code: text(record(value)["code"]) }), { as });
    },

    withdrawInvite(inviteId: string, idempotencyKey: string): Promise<void> {
      return api.send({ method: "DELETE", path: `${base}/invites/${id(inviteId)}`, idempotencyKey, read: () => undefined });
    },

    /** Asks to link with the shop whose code this is; `as` is what THIS shop wants to be. */
    requestLink(code: string, as: LinkRole, idempotencyKey: string): Promise<NetLink> {
      return act("/links", idempotencyKey, linkOf, { code, as });
    },

    link(linkId: string, signal?: AbortSignal): Promise<LinkPage> {
      return api.send({ method: "GET", path: `${base}/links/${id(linkId)}`, signal, read: linkPage });
    },

    /** Accepts a request. Without a counterpart the server makes a row of this shop's books for the partner. */
    acceptLink(linkId: string, counterpartId: string | null, idempotencyKey: string): Promise<NetLink> {
      return act(`/links/${id(linkId)}/accept`, idempotencyKey, linkOf, counterpartId === null ? undefined : { counterpart_id: counterpartId });
    },

    declineLink(linkId: string, idempotencyKey: string): Promise<NetLink> {
      return act(`/links/${id(linkId)}/decline`, idempotencyKey, linkOf);
    },

    endLink(linkId: string, idempotencyKey: string): Promise<NetLink> {
      return act(`/links/${id(linkId)}/end`, idempotencyKey, linkOf);
    },

    /** Names the row of this shop's books an active link stands for, while it has none. */
    setCounterpart(linkId: string, counterpartId: string, idempotencyKey: string): Promise<NetLink> {
      return api.send({
        method: "PUT",
        path: `${base}/links/${id(linkId)}/counterpart`,
        body: { counterpart_id: counterpartId },
        idempotencyKey,
        read: linkOf,
      });
    },

    drafts(signal?: AbortSignal): Promise<Draft[]> {
      return api.send({ method: "GET", path: `${base}/drafts`, signal, read: (value) => list(record(value)["drafts"], draft) });
    },

    draft(draftId: string, signal?: AbortSignal): Promise<Draft> {
      return api.send({ method: "GET", path: `${base}/drafts/${id(draftId)}`, signal, read: draft });
    },

    createDraft(input: DraftInput, idempotencyKey: string): Promise<Draft> {
      return act("/drafts", idempotencyKey, draft, draftBody(input));
    },

    /** Replaces what a draft says, as a whole. */
    updateDraft(draftId: string, input: DraftInput, idempotencyKey: string): Promise<Draft> {
      return api.send({ method: "PUT", path: `${base}/drafts/${id(draftId)}`, body: draftBody(input), idempotencyKey, read: draft });
    },

    deleteDraft(draftId: string, idempotencyKey: string): Promise<void> {
      return api.send({ method: "DELETE", path: `${base}/drafts/${id(draftId)}`, idempotencyKey, read: () => undefined });
    },

    /** Sends a draft to the supplier: it becomes an order, which keeps the draft's id. */
    sendDraft(draftId: string, idempotencyKey: string): Promise<Order> {
      return act(`/drafts/${id(draftId)}/send`, idempotencyKey, order);
    },

    /** `role: "buyer"` lists the orders this shop sent, `"supplier"` the ones it was sent. */
    orders(params: ListParams, signal?: AbortSignal): Promise<Page<OrderSummary>> {
      return api.send({ method: "GET", path: `${base}/orders`, query: listQuery(params), signal, read: paged("orders", orderSummary) });
    },

    order(orderId: string, signal?: AbortSignal): Promise<Order> {
      return api.send({ method: "GET", path: `${base}/orders/${id(orderId)}`, signal, read: order });
    },

    acceptOrder(orderId: string, answer: { currency: Currency | null; lines: readonly AcceptLine[] }, idempotencyKey: string): Promise<Order> {
      const body: Record<string, unknown> = {
        lines: answer.lines.map((line) => {
          const row: Record<string, unknown> = { line_no: line.lineNo, qty: line.qty, unit_price: line.unitPrice };
          if (line.itemId !== null) {
            row["item_id"] = line.itemId;
          }
          return row;
        }),
      };
      if (answer.currency !== null) {
        body["currency"] = answer.currency;
      }
      return act(`/orders/${id(orderId)}/accept`, idempotencyKey, order, body);
    },

    declineOrder(orderId: string, reason: string, idempotencyKey: string): Promise<Order> {
      return act(`/orders/${id(orderId)}/decline`, idempotencyKey, order, { reason });
    },

    cancelOrder(orderId: string, reason: string, idempotencyKey: string): Promise<Order> {
      return act(`/orders/${id(orderId)}/cancel`, idempotencyKey, order, { reason });
    },

    /** Issues the delivery note from the accepted lines. `paid` was handed over on delivery; the rest is on credit. */
    deliver(orderId: string, paid: number | null, idempotencyKey: string): Promise<Note> {
      return act(`/orders/${id(orderId)}/deliver`, idempotencyKey, note, paid === null ? {} : { paid });
    },

    notes(params: ListParams, signal?: AbortSignal): Promise<Page<NoteSummary>> {
      return api.send({ method: "GET", path: `${base}/notes`, query: listQuery(params), signal, read: paged("notes", noteSummary) });
    },

    note(noteId: string, signal?: AbortSignal): Promise<Note> {
      return api.send({ method: "GET", path: `${base}/notes/${id(noteId)}`, signal, read: note });
    },

    /** Issues a new note in place of this one; `lines`, when given, name the order's lines again. */
    correctNote(
      noteId: string,
      change: { reason: string; paid: number | null; lines: readonly { lineNo: number; qty: string; unitPrice: number }[] | null },
      idempotencyKey: string,
    ): Promise<Note> {
      const body: Record<string, unknown> = { reason: change.reason };
      if (change.paid !== null) {
        body["paid"] = change.paid;
      }
      if (change.lines !== null) {
        body["lines"] = change.lines.map((line) => ({ line_no: line.lineNo, qty: line.qty, unit_price: line.unitPrice }));
      }
      return act(`/notes/${id(noteId)}/correct`, idempotencyKey, note, body);
    },

    /**
     * The buyer confirms what was delivered: in one step the goods are received into its stock and the
     * supplier's sale on credit is written. `method` is said only when something was paid on delivery.
     */
    confirmNote(noteId: string, answer: { lines: readonly ConfirmLine[]; method: Method | null }, idempotencyKey: string): Promise<Note> {
      const body: Record<string, unknown> = {};
      if (answer.lines.length > 0) {
        body["lines"] = answer.lines.map((line) => {
          if ("itemId" in line) {
            return { line_no: line.lineNo, item_id: line.itemId };
          }
          const row: Record<string, unknown> = { line_no: line.lineNo, new_price: line.newPrice };
          if (line.newBarcode !== null) {
            row["new_barcode"] = line.newBarcode;
          }
          return row;
        });
      }
      if (answer.method !== null) {
        body["method"] = answer.method;
      }
      return act(`/notes/${id(noteId)}/confirm`, idempotencyKey, note, body);
    },

    rejectNote(
      noteId: string,
      answer: { reason: string; lines: readonly { lineNo: number; receivedQty: string }[] },
      idempotencyKey: string,
    ): Promise<Note> {
      const body: Record<string, unknown> = { reason: answer.reason };
      if (answer.lines.length > 0) {
        body["lines"] = answer.lines.map((line) => ({ line_no: line.lineNo, received_qty: line.receivedQty }));
      }
      return act(`/notes/${id(noteId)}/reject`, idempotencyKey, note, body);
    },

    payments(params: Omit<ListParams, "role">, signal?: AbortSignal): Promise<Page<Payment>> {
      return api.send({ method: "GET", path: `${base}/payments`, query: listQuery(params), signal, read: paged("payments", payment) });
    },

    payment(paymentId: string, signal?: AbortSignal): Promise<Payment> {
      return api.send({ method: "GET", path: `${base}/payments/${id(paymentId)}`, signal, read: payment });
    },

    /** Either side records that the buyer paid the supplier: in this shop's books at once, awaiting the partner. */
    recordPayment(
      input: { linkId: string; amount: number; currency: Currency; method: Method | null; note: string | null },
      idempotencyKey: string,
    ): Promise<Payment> {
      const body: Record<string, unknown> = { link_id: input.linkId, amount: input.amount, currency: input.currency };
      if (input.method !== null) {
        body["method"] = input.method;
      }
      if (input.note !== null && input.note !== "") {
        body["note"] = input.note;
      }
      return act("/payments", idempotencyKey, payment, body);
    },

    confirmPayment(paymentId: string, method: Method | null, idempotencyKey: string): Promise<Payment> {
      return act(`/payments/${id(paymentId)}/confirm`, idempotencyKey, payment, method === null ? {} : { method });
    },

    declinePayment(paymentId: string, reason: string, idempotencyKey: string): Promise<Payment> {
      return act(`/payments/${id(paymentId)}/decline`, idempotencyKey, payment, { reason });
    },

    withdrawPayment(paymentId: string, idempotencyKey: string): Promise<Payment> {
      return act(`/payments/${id(paymentId)}/withdraw`, idempotencyKey, payment);
    },
  };
}

export type NetworkApi = ReturnType<typeof networkOf>;
