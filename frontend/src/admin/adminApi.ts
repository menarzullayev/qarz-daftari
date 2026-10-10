import { type ApiAuth, type ApiError, call, type Call, type Fetch, type Page, reading, type Transport } from "../shared/api";

/**
 * The administrator's side of the API, under /api/admin/v1 (backend/src/qarz/interface/admin_api.py).
 *
 * It is reached with the same session as the web panel (the HTTP-only cookie and the CSRF token from
 * Telegram sign-in) and, behind the door, with a second cookie the server sets once a code from the
 * authenticator is accepted. Someone who is not an administrator is answered "not found" on every route
 * here, exactly as for a route that does not exist.
 *
 * Nothing in this file returns a shop's customers, entries or balances. The API gives them in one place
 * only, to an administrator whose support access to that shop is open (REQ-059), and the calls for it
 * live with the support-access screens (`support/customers.ts`), built on `send`. No other call for
 * them may be added.
 */

const { record, text, textOrNull, whole, wholeOrNull, flag, list } = reading;
const BASE = "/api/admin/v1";
const segment = encodeURIComponent;

/** Where the caller stands at the door: which step the panel shows. */
export type AuthStatus = {
  /** A second factor was created for this person. */
  enrolled: boolean;
  /** A first code from it was accepted; until then it can be created again. */
  confirmed: boolean;
  /** An admin session is open in this browser. */
  elevated: boolean;
  expiresAt: string | null;
  /** While this instant is in the future no code is looked at. */
  lockedUntil: string | null;
};

/** `otpauthUri` is the secret, shown once; null when the server answers a repeated request. */
export type Enrolment = { otpauthUri: string | null };

export type SubscriptionState = {
  /** What applies today. */
  state: string;
  /** The row as written, which a finished period outlives until the daily review. */
  storedState: string;
  trialEnds: string | null;
  paidThrough: string | null;
  priorState: string | null;
};

export type AdminShop = {
  id: string;
  name: string;
  status: string;
  createdAt: string;
  subscription: SubscriptionState;
  ownerTgId: number | null;
  staffCount: number;
  customerCount: number;
  /**
   * With the free plan switched on: how many customers the plan holds and how many of the shop's count
   * for it (the archived do not). Null while the plan is off: the server then sends no such field, and
   * no state is "free".
   */
  plan: { freeCustomers: number; customers: number } | null;
};

export type SubscriptionReceipt = {
  id: string;
  statedAmount: number | null;
  status: string;
  months: number | null;
  rejectReason: string | null;
  createdAt: string;
  decidedAt: string | null;
};

/**
 * A subscription receipt as the administrator's queue has it (backend/src/qarz/application/
 * admin_receipts.py, `receipt_body`): the shop, what its owner stated, and what was decided. Nothing of
 * a shop's customers is in it.
 */
export type AdminReceipt = {
  id: string;
  shopId: string;
  shopName: string;
  statedAmount: number | null;
  statedMonths: number | null;
  /** The server's word: "submitted", "approved" or "rejected". */
  status: string;
  months: number | null;
  rejectReason: string | null;
  createdAt: string;
  decidedAt: string | null;
  decidedBy: string | null;
  /**
   * The Telegram identifier of the review group's administrator who decided, when it was not an
   * administrator of the platform; then `decidedBy` is null.
   */
  decidedByTgId: number | null;
  /**
   * The card the owner says they paid to, as its label and last four digits: which account's statement
   * to look at. Null for a receipt that names none.
   */
  paidToCard: string | null;
  hasFile: boolean;
};

/** `copies`: how many other receipts, of any shop, carry the same file. */
export type QueuedReceipt = AdminReceipt & { copies: number };

export type ReceiptCopy = { id: string; shopId: string; shopName: string; statedAmount: number | null; status: string; createdAt: string };

export type AdminReceiptDetail = AdminReceipt & {
  /** Where the file can be opened for five minutes; null when there is none or it cannot be served now. */
  file: { url: string; expiresAt: string } | null;
  copies: ReceiptCopy[];
};

/** A decided receipt; an approval also says where the shop's subscription now stands. */
export type DecidedReceipt = AdminReceipt & { subscription: { state: string; paidThrough: string } | null };

export type AuditRow = {
  id: string;
  at: string;
  /** Who acted: an administrator, or a Telegram administrator of the review group. Exactly one is set. */
  adminId: string | null;
  actorTgId: number | null;
  action: string;
  targetType: string;
  targetId: string | null;
  shopId: string | null;
  reason: string | null;
  /** What the action changed, as the server recorded it; shown as it is. */
  detail: Readonly<Record<string, unknown>>;
};

export type AdminShopDetail = AdminShop & {
  lang: string;
  deletionDue: string | null;
  receipts: SubscriptionReceipt[];
  /** Changes administrators made to this shop's subscription, newest first. */
  changes: AuditRow[];
};

/** A card owners may pay the subscription to: sixteen digits and a short name for it. */
export type PaymentCard = { number: string; label: string };
export type SettingValue = boolean | number | string | PaymentCard[] | null;
export type PlatformSettings = {
  values: Readonly<Record<string, SettingValue>>;
  /** The settings whose change asks for a code from the authenticator again. */
  needsCode: readonly string[];
  /** Who last changed a setting and when, for the ones that were ever changed. */
  changed: Readonly<Record<string, { by: string; at: string }>>;
  /**
   * In the answer to a change that lowered how many customers the free plan holds, or switched the plan
   * off: how many shops became limited by it. Their owners were told. Null in every other answer.
   */
  planLimited: number | null;
};

/**
 * A support access: one administrator may read one shop for a few hours, for a stated reason
 * (backend/src/qarz/application/support_access.py, `access_body`).
 */
export type SupportAccess = {
  id: string;
  shopId: string;
  /** Given by the list across shops; null where the server names no shop. */
  shopName: string | null;
  adminId: string;
  reason: string;
  /** As the server saw it when it answered: "active", "expired" or "closed". */
  state: string;
  startsAt: string;
  endsAt: string;
  closedAt: string | null;
  /** Who ended it before its time: "owner" or "admin"; null when nobody did. */
  closedBy: string | null;
};

export const SUBSCRIPTION_ACTIONS = ["trial", "endTrial", "paidThrough", "suspend", "unsuspend"] as const;
export type SubscriptionAction = (typeof SUBSCRIPTION_ACTIONS)[number];

function authStatus(value: unknown): AuthStatus {
  const body = record(value);
  return {
    enrolled: flag(body["enrolled"]),
    confirmed: flag(body["confirmed"]),
    elevated: flag(body["elevated"]),
    expiresAt: textOrNull(body["expires_at"]),
    lockedUntil: textOrNull(body["locked_until"]),
  };
}

function shopPlan(value: unknown): { freeCustomers: number; customers: number } {
  const plan = record(value);
  return { freeCustomers: whole(plan["free_customers"]), customers: whole(plan["customers"]) };
}

function adminShop(value: unknown): AdminShop {
  const body = record(value);
  const subscription = record(body["subscription"]);
  const owner = body["owner_tg_id"];
  if (owner !== null && owner !== undefined && typeof owner !== "number") {
    throw new TypeError("owner identifier");
  }
  return {
    id: text(body["id"]),
    name: text(body["name"]),
    status: text(body["status"]),
    createdAt: text(body["created_at"]),
    subscription: {
      state: text(subscription["state"]),
      storedState: text(subscription["stored_state"]),
      trialEnds: textOrNull(subscription["trial_ends"]),
      paidThrough: textOrNull(subscription["paid_through"]),
      priorState: textOrNull(subscription["prior_state"]),
    },
    ownerTgId: owner ?? null,
    staffCount: whole(body["staff_count"]),
    customerCount: whole(body["customer_count"]),
    plan: body["plan"] === undefined || body["plan"] === null ? null : shopPlan(body["plan"]),
  };
}

/** A Telegram user identifier, or null where the server sends none. */
function telegramIdOrNull(value: unknown): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value <= 0) {
    throw new TypeError("telegram identifier");
  }
  return value;
}

function auditRow(value: unknown): AuditRow {
  const body = record(value);
  return {
    id: text(body["id"]),
    at: text(body["at"]),
    adminId: textOrNull(body["admin_id"]),
    actorTgId: telegramIdOrNull(body["actor_tg_id"]),
    action: text(body["action"]),
    targetType: text(body["target_type"]),
    targetId: textOrNull(body["target_id"]),
    shopId: textOrNull(body["shop_id"]),
    reason: textOrNull(body["reason"]),
    detail: body["detail"] === null || body["detail"] === undefined ? {} : record(body["detail"]),
  };
}

function supportAccess(value: unknown): SupportAccess {
  const body = record(value);
  return {
    id: text(body["id"]),
    shopId: text(body["shop_id"]),
    shopName: textOrNull(body["shop_name"]),
    adminId: text(body["admin_id"]),
    reason: text(body["reason"]),
    state: text(body["state"]),
    startsAt: text(body["starts_at"]),
    endsAt: text(body["ends_at"]),
    closedAt: textOrNull(body["closed_at"]),
    closedBy: textOrNull(body["closed_by"]),
  };
}

function adminReceipt(value: unknown): AdminReceipt {
  const body = record(value);
  return {
    id: text(body["id"]),
    shopId: text(body["shop_id"]),
    shopName: text(body["shop_name"]),
    statedAmount: wholeOrNull(body["stated_amount"]),
    statedMonths: wholeOrNull(body["stated_months"]),
    status: text(body["status"]),
    months: wholeOrNull(body["months"]),
    rejectReason: textOrNull(body["reject_reason"]),
    createdAt: text(body["created_at"]),
    decidedAt: textOrNull(body["decided_at"]),
    decidedBy: textOrNull(body["decided_by"]),
    decidedByTgId: telegramIdOrNull(body["decided_by_tg_id"]),
    paidToCard: textOrNull(body["paid_to_card"]),
    hasFile: flag(body["has_file"]),
  };
}

/**
 * What a shop proposed for the shared catalogue: an item it added by hand, or a barcode it attached to
 * an item it had picked. The server never says which shop it came from, and it carries no price.
 */
export type CatalogSuggestion = {
  id: string;
  kind: string;
  /** An item: the name and the unit the shop gave it. */
  name: string | null;
  unit: string | null;
  barcode: string | null;
  /** A barcode: the catalogue item it is proposed for. An approved item: the item it became. */
  sharedItem: { id: string; nameRu: string | null; nameUz: string | null; amount: string | null } | null;
  status: string;
  createdAt: string;
  /** How many other shops wait with the same name, or the same barcode. */
  same: number;
};
export type SuggestionNames = { nameRu: string | null; nameUz: string | null; category: string };

function catalogSuggestion(value: unknown): CatalogSuggestion {
  const body = record(value);
  const item = body["shared_item"] === null || body["shared_item"] === undefined ? null : record(body["shared_item"]);
  return {
    id: text(body["id"]),
    kind: text(body["kind"]),
    name: textOrNull(body["name"]),
    unit: textOrNull(body["unit"]),
    barcode: textOrNull(body["barcode"]),
    sharedItem:
      item === null
        ? null
        : { id: text(item["id"]), nameRu: textOrNull(item["name_ru"]), nameUz: textOrNull(item["name_uz"]), amount: textOrNull(item["amount"]) },
    status: text(body["status"]),
    createdAt: text(body["created_at"]),
    same: whole(body["same"]),
  };
}

/** The answer to a decision: what the suggestion is now. */
function decidedSuggestion(value: unknown): { id: string; status: string } {
  const body = record(value);
  return { id: text(body["id"]), status: text(body["status"]) };
}

function queuedReceipt(value: unknown): QueuedReceipt {
  return { ...adminReceipt(value), copies: whole(record(value)["copies"]) };
}

function receiptDetail(value: unknown): AdminReceiptDetail {
  const body = record(value);
  const file = body["file"] === null ? null : record(body["file"]);
  return {
    ...adminReceipt(body),
    file: file === null ? null : { url: text(file["url"]), expiresAt: text(file["expires_at"]) },
    copies: list(body["copies"], (element) => {
      const copy = record(element);
      return {
        id: text(copy["id"]),
        shopId: text(copy["shop_id"]),
        shopName: text(copy["shop_name"]),
        statedAmount: wholeOrNull(copy["stated_amount"]),
        status: text(copy["status"]),
        createdAt: text(copy["created_at"]),
      };
    }),
  };
}

function decidedReceipt(value: unknown): DecidedReceipt {
  const body = record(value);
  const after = body["subscription"] === undefined || body["subscription"] === null ? null : record(body["subscription"]);
  return {
    ...adminReceipt(body),
    subscription: after === null ? null : { state: text(after["state"]), paidThrough: text(after["paid_through"]) },
  };
}

function shopDetail(value: unknown): AdminShopDetail {
  const body = record(value);
  return {
    ...adminShop(body),
    lang: text(body["lang"]),
    deletionDue: textOrNull(body["deletion_due"]),
    receipts: list(body["receipts"], (element) => {
      const receipt = record(element);
      return {
        id: text(receipt["id"]),
        statedAmount: wholeOrNull(receipt["stated_amount"]),
        status: text(receipt["status"]),
        months: wholeOrNull(receipt["months"]),
        rejectReason: textOrNull(receipt["reject_reason"]),
        createdAt: text(receipt["created_at"]),
        decidedAt: textOrNull(receipt["decided_at"]),
      };
    }),
    changes: list(body["changes"], auditRow),
  };
}

function settingValue(value: unknown): SettingValue {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return value;
  }
  if (Array.isArray(value)) {
    // The cards to pay to: the only setting that is a list.
    return list(value, (item) => ({ number: text(record(item)["number"]), label: text(record(item)["label"]) }));
  }
  return whole(value);
}

function platformSettings(value: unknown): PlatformSettings {
  const body = record(value);
  const values: Record<string, SettingValue> = {};
  for (const [key, stored] of Object.entries(record(body["settings"]))) {
    values[key] = settingValue(stored);
  }
  const changed: Record<string, { by: string; at: string }> = {};
  for (const [key, entry] of Object.entries(record(body["changed"] ?? {}))) {
    const who = record(entry);
    changed[key] = { by: text(who["by"]), at: text(who["at"]) };
  }
  return { values, needsCode: list(body["needs_code"], text), changed, planLimited: shopsLimited(body["free_plan_off"] ?? body["free_plan_lowered"]) };
}

/** `shops_limited` of the server's word on a free plan lowered or switched off; null where it says nothing of one. */
function shopsLimited(value: unknown): number | null {
  return value === undefined || value === null ? null : whole(record(value)["shops_limited"]);
}

const ACTION_PATHS: Readonly<Record<SubscriptionAction, string>> = {
  trial: "trial",
  endTrial: "trial/end",
  paidThrough: "paid-through",
  suspend: "suspend",
  unsuspend: "unsuspend",
};

export function createAdminApi(options: {
  fetch: Fetch;
  auth: ApiAuth;
  onUnauthenticated?: () => void;
  onRefusal?: (error: ApiError) => void;
}) {
  const transport: Transport = options;
  return {
    /** Where every call of the administrator's side lives. */
    base: BASE,

    /**
     * One request of a call that is not built here: the support-access screens read a shop's customers
     * through it, with the same headers, errors and refusal hooks as every other call.
     */
    send<T>(request: Call<T>): Promise<T> {
      return call(transport, request);
    },

    readAuth(signal?: AbortSignal): Promise<AuthStatus> {
      return call(transport, { method: "GET", path: `${BASE}/auth`, signal, read: authStatus });
    },

    /** Creates the caller's second factor. Until a first code is accepted it may be created again. */
    enrol(idempotencyKey: string): Promise<Enrolment> {
      return call(transport, {
        method: "POST",
        path: `${BASE}/auth/enrolment`,
        idempotencyKey,
        read: (value) => ({ otpauthUri: textOrNull(record(value)["otpauth_uri"]) }),
      });
    },

    /** Opens the admin session with a code from the authenticator. A code is used once and is never kept. */
    openSession(code: string): Promise<void> {
      return call(transport, { method: "POST", path: `${BASE}/auth/session`, body: { code }, read: () => undefined });
    },

    closeSession(): Promise<void> {
      return call(transport, { method: "DELETE", path: `${BASE}/auth/session`, read: () => undefined });
    },

    listShops(
      params: { q?: string | null; state?: string | null; cursor?: string | null },
      signal?: AbortSignal,
    ): Promise<Page<AdminShop>> {
      return call(transport, {
        method: "GET",
        path: `${BASE}/shops`,
        query: { q: params.q, state: params.state, cursor: params.cursor },
        signal,
        read: reading.page(adminShop),
      });
    },

    /** One shop with its subscription receipts and change history. The server records that it was looked at. */
    readShop(shopId: string, signal?: AbortSignal): Promise<AdminShopDetail> {
      return call(transport, { method: "GET", path: `${BASE}/shops/${segment(shopId)}`, signal, read: shopDetail });
    },

    /**
     * Changes a shop's subscription. Every change needs a reason; `date` is the trial's last day or the
     * last paid day for the two actions that set one.
     */
    changeSubscription(
      shopId: string,
      action: SubscriptionAction,
      input: { reason: string; date: string | null },
      idempotencyKey: string,
    ): Promise<AdminShop> {
      const body: Record<string, string> = { reason: input.reason };
      if (action === "trial" || action === "paidThrough") {
        if (input.date === null) {
          throw new RangeError("this change needs a date");
        }
        body[action === "trial" ? "trial_ends" : "paid_through"] = input.date;
      }
      return call(transport, {
        method: "POST",
        path: `${BASE}/shops/${segment(shopId)}/${ACTION_PATHS[action]}`,
        body,
        idempotencyKey,
        read: adminShop,
      });
    },

    /** Receipts of one status across all shops, oldest first; those awaiting a decision by default. */
    listReceipts(params: { status?: string | null; cursor?: string | null }, signal?: AbortSignal): Promise<Page<QueuedReceipt>> {
      return call(transport, {
        method: "GET",
        path: `${BASE}/receipts`,
        query: { status: params.status, cursor: params.cursor },
        signal,
        read: reading.page(queuedReceipt),
      });
    },

    /**
     * One receipt, the receipts that carry the same file, and a link to the file valid five minutes.
     * The server records that it was looked at.
     */
    readReceipt(receiptId: string, signal?: AbortSignal): Promise<AdminReceiptDetail> {
      return call(transport, { method: "GET", path: `${BASE}/receipts/${segment(receiptId)}`, signal, read: receiptDetail });
    },

    /** Approves a waiting receipt for `months`; the note, when one is written, goes to the audit. */
    approveReceipt(receiptId: string, input: { months: number; note: string | null }, idempotencyKey: string): Promise<DecidedReceipt> {
      if (!Number.isSafeInteger(input.months)) {
        throw new RangeError("months must be a whole number");
      }
      const body: Record<string, unknown> = { months: input.months };
      if (input.note !== null) {
        body["reason"] = input.note;
      }
      return call(transport, {
        method: "POST",
        path: `${BASE}/receipts/${segment(receiptId)}/approve`,
        body,
        idempotencyKey,
        read: decidedReceipt,
      });
    },

    /** Rejects a waiting receipt. The reason is told to the shop's owner. */
    rejectReceipt(receiptId: string, reason: string, idempotencyKey: string): Promise<DecidedReceipt> {
      return call(transport, {
        method: "POST",
        path: `${BASE}/receipts/${segment(receiptId)}/reject`,
        body: { reason },
        idempotencyKey,
        read: decidedReceipt,
      });
    },

    /** What shops proposed for the shared catalogue, in one status, oldest first. Behind `catalog_on`. */
    listSuggestions(params: { status?: string | null; cursor?: string | null }, signal?: AbortSignal): Promise<Page<CatalogSuggestion>> {
      return call(transport, {
        method: "GET",
        path: `${BASE}/catalog/suggestions`,
        query: { status: params.status, cursor: params.cursor },
        signal,
        read: reading.page(catalogSuggestion),
      });
    },

    /**
     * Approves a waiting suggestion. An item gets the names and the category given here (at least one
     * name); a barcode needs none and `names` is null.
     */
    approveSuggestion(suggestionId: string, names: SuggestionNames | null, idempotencyKey: string): Promise<{ id: string; status: string }> {
      const path = `${BASE}/catalog/suggestions/${segment(suggestionId)}/approve`;
      if (names === null) {
        return call(transport, { method: "POST", path, idempotencyKey, read: decidedSuggestion });
      }
      const body: Record<string, unknown> = { category: names.category };
      if (names.nameRu !== null) {
        body["name_ru"] = names.nameRu;
      }
      if (names.nameUz !== null) {
        body["name_uz"] = names.nameUz;
      }
      return call(transport, { method: "POST", path, body, idempotencyKey, read: decidedSuggestion });
    },

    /** Rejects a waiting suggestion. The shop's own item is untouched and the shop is not told. */
    rejectSuggestion(suggestionId: string, idempotencyKey: string): Promise<{ id: string; status: string }> {
      return call(transport, {
        method: "POST",
        path: `${BASE}/catalog/suggestions/${segment(suggestionId)}/reject`,
        idempotencyKey,
        read: decidedSuggestion,
      });
    },

    readSettings(signal?: AbortSignal): Promise<PlatformSettings> {
      return call(transport, { method: "GET", path: `${BASE}/settings`, signal, read: platformSettings });
    },

    /**
     * How many of the shops the free plan holds today would become limited if it held `customers`.
     * Nothing is changed by asking. Null when the server says nothing: the plan is switched off.
     */
    previewFreePlan(customers: number, signal?: AbortSignal): Promise<number | null> {
      return call(transport, {
        method: "GET",
        path: `${BASE}/settings`,
        query: { free_plan_customers: String(customers) },
        signal,
        read: (value) => shopsLimited(record(value)["free_plan_preview"]),
      });
    },

    /**
     * How many shops the free plan holds today: all of them would become limited if it were switched off.
     * Nothing is changed by asking. Null when the server says nothing: the plan is switched off already.
     */
    previewFreePlanOff(signal?: AbortSignal): Promise<number | null> {
      return call(transport, {
        method: "GET",
        path: `${BASE}/settings`,
        query: { free_plan_on: "false" },
        signal,
        read: (value) => shopsLimited(record(value)["free_plan_off_preview"]),
      });
    },

    /**
     * Gives the shop to the person with this Telegram identifier (an owner who lost their account).
     * Needs a reason and, every time, a fresh code from the authenticator.
     */
    reassignOwner(
      shopId: string,
      input: { newOwnerTgId: number; reason: string; code: string },
      idempotencyKey: string,
    ): Promise<AdminShop> {
      return call(transport, {
        method: "POST",
        path: `${BASE}/shops/${encodeURIComponent(shopId)}/owner`,
        body: { new_owner_tg_id: input.newOwnerTgId, reason: input.reason, code: input.code },
        idempotencyKey,
        read: adminShop,
      });
    },

    /** Changes platform settings. `code` is asked for again when a sensitive one is among them. */
    updateSettings(
      changes: Readonly<Record<string, SettingValue>>,
      code: string | null,
      reason: string | null,
      idempotencyKey: string,
    ): Promise<PlatformSettings> {
      const body: Record<string, unknown> = { changes };
      if (code !== null) {
        body["code"] = code;
      }
      if (reason !== null) {
        body["reason"] = reason;
      }
      return call(transport, { method: "PATCH", path: `${BASE}/settings`, body, idempotencyKey, read: platformSettings });
    },

    /**
     * Opens the caller's support access to a shop for `hours` (1 to 24). The owner is told the reason
     * and the time it ends. One administrator has at most one open access to a shop.
     */
    openSupport(shopId: string, input: { reason: string; hours: number }, idempotencyKey: string): Promise<SupportAccess> {
      return call(transport, {
        method: "POST",
        path: `${BASE}/shops/${segment(shopId)}/support-access`,
        body: { reason: input.reason, hours: input.hours },
        idempotencyKey,
        read: supportAccess,
      });
    },

    /** Ends the caller's own open access to the shop before its time. */
    closeSupport(shopId: string, idempotencyKey: string): Promise<void> {
      return call(transport, {
        method: "POST",
        path: `${BASE}/shops/${segment(shopId)}/support-access/close`,
        idempotencyKey,
        read: () => undefined,
      });
    },

    /** Support accesses across shops, whoever opened them, newest first. */
    listSupport(
      params: { shopId?: string | null; open?: boolean; cursor?: string | null },
      signal?: AbortSignal,
    ): Promise<Page<SupportAccess>> {
      return call(transport, {
        method: "GET",
        path: `${BASE}/support-access`,
        query: { shop_id: params.shopId, open: params.open ? "true" : null, cursor: params.cursor },
        signal,
        read: reading.page(supportAccess),
      });
    },

    listAudit(
      params: { shopId?: string | null; action?: string | null; adminId?: string | null; cursor?: string | null },
      signal?: AbortSignal,
    ): Promise<Page<AuditRow>> {
      return call(transport, {
        method: "GET",
        path: `${BASE}/audit`,
        query: { shop_id: params.shopId, action: params.action, admin_id: params.adminId, cursor: params.cursor },
        signal,
        read: reading.page(auditRow),
      });
    },
  };
}

export type AdminApi = ReturnType<typeof createAdminApi>;
