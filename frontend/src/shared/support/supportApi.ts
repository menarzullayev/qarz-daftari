import { type Page, reading, type ShopApi } from "../api";

/**
 * Support access as the shop's owner sees it (REQ-059): which administrator may read the shop, why and
 * until when, and the way to end it. Built on the shop API's `send`, outside the first load. Shapes
 * follow backend/src/qarz/application/support_access.py (`access_body`) and interface/support_api.py.
 */

const { record, text, textOrNull } = reading;

export type SupportAccess = {
  id: string;
  /** The administrator. The API gives no name for one, only the identifier. */
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

export function readSupportAccess(value: unknown): SupportAccess {
  const body = record(value);
  return {
    id: text(body["id"]),
    adminId: text(body["admin_id"]),
    reason: text(body["reason"]),
    state: text(body["state"]),
    startsAt: text(body["starts_at"]),
    endsAt: text(body["ends_at"]),
    closedAt: textOrNull(body["closed_at"]),
    closedBy: textOrNull(body["closed_by"]),
  };
}

/**
 * Whether the access lets its administrator read the shop at `now`. The server's word is from the
 * moment it answered; an access whose time has run out since then is no longer open.
 */
export function isOpen(access: { state: string; endsAt: string }, now: Date): boolean {
  const ends = new Date(access.endsAt).getTime();
  return access.state === "active" && !Number.isNaN(ends) && ends > now.getTime();
}

/** A short code for an administrator: the end of the identifier, as for a member of staff. */
export function adminCode(adminId: string): string {
  return adminId.slice(-6);
}

export function supportOf(api: ShopApi) {
  const path = `${api.base}/support-access`;
  return {
    /** Every support access the shop has had, newest first, a page at a time. The owner's alone. */
    list(cursor: string | null, signal?: AbortSignal): Promise<Page<SupportAccess>> {
      return api.send({ method: "GET", path, query: { cursor }, signal, read: reading.page(readSupportAccess) });
    },

    /** Ends an open access now. */
    end(accessId: string, idempotencyKey: string): Promise<SupportAccess> {
      return api.send({
        method: "POST",
        path: `${path}/${encodeURIComponent(accessId)}/end`,
        idempotencyKey,
        read: readSupportAccess,
      });
    },
  };
}

export type SupportApi = ReturnType<typeof supportOf>;
