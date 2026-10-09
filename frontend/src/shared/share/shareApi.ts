import { reading, type ShopApi } from "../api";

/**
 * A customer's secret read-only link, for managers and owners (the expansion of 2026-10-09, module B).
 * Built on the shop API's `send` in a module that is loaded apart, so none of it is part of the first
 * load. Shapes follow backend/src/qarz/application/customer_shares.py.
 *
 * All of it is behind a platform switch. While the switch is off every call here is answered 404, the
 * same as for a route that does not exist, and the sections show nothing: they appear only once the
 * server has answered in the shapes below.
 */

const { record, text, textOrNull, flag } = reading;

/** Where the page behind a link is served. The secret follows "#": a fragment is never sent to a server. */
export const SHARE_PAGE = "/k/";

/** What the server tells staff about a customer's link. Never the link itself: it is not kept. */
export type ShareState = {
  exists: boolean;
  expired: boolean;
  createdAt: string | null;
  expiresAt: string | null;
  lastOpenedAt: string | null;
};

/**
 * A link that was just made. `token` is a credential shown once: it lives in component state only and is
 * never written to storage or to a log. Null when the server answers a repeated request, whose stored
 * answer no longer carries it.
 */
export type IssuedShare = { token: string | null; expiresAt: string };

export type ShareContact = { phone: string | null };

// 32 random bytes in the URL-safe alphabet (backend/src/qarz/domain/customer_share.py).
const TOKEN_SHAPE = /^[A-Za-z0-9_-]{43}$/;

/** The address to hand to the customer. Anything that is not a token is refused, so no other text ends up in a link. */
export function shareUrl(origin: string, token: string): string {
  if (!TOKEN_SHAPE.test(token)) {
    throw new RangeError("not a share token");
  }
  return `${origin.replace(/\/+$/, "")}${SHARE_PAGE}#${token}`;
}

function shareState(value: unknown): ShareState {
  const body = record(value);
  return {
    exists: flag(body["exists"]),
    expired: flag(body["expired"]),
    createdAt: textOrNull(body["created_at"]),
    expiresAt: textOrNull(body["expires_at"]),
    lastOpenedAt: textOrNull(body["last_opened_at"]),
  };
}

function issuedShare(value: unknown): IssuedShare {
  const body = record(value);
  const token = textOrNull(body["token"]);
  if (token !== null && !TOKEN_SHAPE.test(token)) {
    throw new RangeError("not a share token");
  }
  return { token, expiresAt: text(body["expires_at"]) };
}

function shareContact(value: unknown): ShareContact {
  const body = record(value);
  // The field is always there, null when no phone is set: an answer without it is some other answer.
  if (!Object.hasOwn(body, "phone")) {
    throw new RangeError("not a share contact");
  }
  return { phone: textOrNull(body["phone"]) };
}

export function sharesOf(api: ShopApi) {
  const of = (customerId: string) => `${api.base}/customers/${encodeURIComponent(customerId)}/share`;
  const contact = `${api.base}/share-contact`;
  return {
    read(customerId: string, signal?: AbortSignal): Promise<ShareState> {
      return api.send({ method: "GET", path: of(customerId), signal, read: shareState });
    },

    /** Makes the customer's link and ends the one before it. The answer carries the token once. */
    create(customerId: string, idempotencyKey: string): Promise<IssuedShare> {
      return api.send({ method: "POST", path: of(customerId), idempotencyKey, read: issuedShare });
    },

    revoke(customerId: string, idempotencyKey: string): Promise<void> {
      return api.send({ method: "DELETE", path: of(customerId), idempotencyKey, read: () => undefined });
    },

    readContact(signal?: AbortSignal): Promise<ShareContact> {
      return api.send({ method: "GET", path: contact, signal, read: shareContact });
    },

    /** Sets the phone customers see on the page; null clears it. The owner only. */
    setContact(phone: string | null, idempotencyKey: string): Promise<ShareContact> {
      return api.send({ method: "PUT", path: contact, body: { phone }, idempotencyKey, read: shareContact });
    },
  };
}

export type SharesApi = ReturnType<typeof sharesOf>;
