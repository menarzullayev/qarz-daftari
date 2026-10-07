import { type ApiAuth, type ApiError, call, type Fetch, type Page, reading, type Transport } from "../shared/api";

/**
 * The administrator's side of the API, under /api/admin/v1 (backend/src/qarz/interface/admin_api.py).
 *
 * It is reached with the same session as the web panel (the HTTP-only cookie and the CSRF token from
 * Telegram sign-in) and, behind the door, with a second cookie the server sets once a code from the
 * authenticator is accepted. Someone who is not an administrator is answered "not found" on every route
 * here, exactly as for a route that does not exist.
 *
 * Nothing here returns a shop's customers, entries or balances: the API does not give them (REQ-059),
 * and no call for them may be added.
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

export type AuditRow = {
  id: string;
  at: string;
  adminId: string;
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

export type SettingValue = boolean | number | string | null;
export type PlatformSettings = {
  values: Readonly<Record<string, SettingValue>>;
  /** The settings whose change asks for a code from the authenticator again. */
  needsCode: readonly string[];
  /** Who last changed a setting and when, for the ones that were ever changed. */
  changed: Readonly<Record<string, { by: string; at: string }>>;
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
  };
}

function auditRow(value: unknown): AuditRow {
  const body = record(value);
  return {
    id: text(body["id"]),
    at: text(body["at"]),
    adminId: text(body["admin_id"]),
    action: text(body["action"]),
    targetType: text(body["target_type"]),
    targetId: textOrNull(body["target_id"]),
    shopId: textOrNull(body["shop_id"]),
    reason: textOrNull(body["reason"]),
    detail: body["detail"] === null || body["detail"] === undefined ? {} : record(body["detail"]),
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
  return { values, needsCode: list(body["needs_code"], text), changed };
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

    readSettings(signal?: AbortSignal): Promise<PlatformSettings> {
      return call(transport, { method: "GET", path: `${BASE}/settings`, signal, read: platformSettings });
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

    listAudit(
      params: { shopId?: string | null; action?: string | null; cursor?: string | null },
      signal?: AbortSignal,
    ): Promise<Page<AuditRow>> {
      return call(transport, {
        method: "GET",
        path: `${BASE}/audit`,
        query: { shop_id: params.shopId, action: params.action, cursor: params.cursor },
        signal,
        read: reading.page(auditRow),
      });
    },
  };
}

export type AdminApi = ReturnType<typeof createAdminApi>;
