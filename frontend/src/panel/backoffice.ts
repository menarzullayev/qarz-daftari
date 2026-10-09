import { type Page, reading, type ShopApi } from "../shared/api";
import { isRole, type Role } from "../shared/navigation";

/**
 * The owner's back office of a shop: staff, invitations, ownership transfer, the activity log, shop
 * deletion, and the totals of every shop a person owns. These calls are the web panel's and are built
 * here, on the shop API's `send`, so they are not part of the Mini App's first load. Shapes follow
 * backend/src/qarz/interface/staff_api.py, account_api.py, shop_deletion_api.py and me_api.py.
 */

const { record, text, textOrNull, whole, list } = reading;

export type Member = {
  /** The membership: what the activity log names as the actor and an entry as its author. */
  id: string;
  role: Role;
  /** "active" or "suspended"; removed members are not listed. */
  status: string;
};

export type Invitation = { id: string; role: string; expiresAt: string };

/**
 * An invitation as the server answers when it is created. `token` follows "s_" in the bot's deep link
 * and is a credential shown once: it lives in component state only. Null when the server answers a
 * repeated request, whose stored answer no longer carries it.
 */
export type IssuedInvitation = Invitation & { token: string | null };

export type Transfer = { id: string; fromMembership: string; toMembership: string; status: string; expiresAt: string };

/** `status`: "active", or "deletion_pending" with the instant the shop's data is erased. */
export type Deletion = { status: string; due: string | null };

export type Activity = {
  id: string;
  at: string;
  /** "staff", "customer" or "system". */
  actorKind: string;
  /** The membership that acted; null for a customer or the system. */
  actorId: string | null;
  action: string;
  subjectType: string;
  subjectId: string | null;
};

/** Whole UZS, and a count of customers. */
export type Totals = { outstanding: number; debtors: number; overdue: number; dueToday: number };
/** `usd`: the same four figures of the dollar debts, in cents, for a shop that works in dollars. */
export type ShopTotals = Totals & { shopId: string; name: string; usd?: Totals };
/** `total.usd`: the dollars of the shops that work in them, added to each other and to nothing else. */
export type OwnerTotals = { items: ShopTotals[]; total: Totals & { usd?: Totals } };

export type MemberPatch = { role?: "manager" | "seller"; status?: "active" | "suspended" };
export type ActivityFilter = {
  /** A membership. */
  actor?: string | null;
  /** The start of an action's name: "customer" matches "customer.created". */
  action?: string | null;
  /** What the action was about, for example a customer. */
  subject?: string | null;
  cursor?: string | null;
};

function member(value: unknown): Member {
  const body = record(value);
  const role = body["role"];
  if (!isRole(role)) {
    throw new TypeError("unknown role");
  }
  return { id: text(body["id"]), role, status: text(body["status"]) };
}

function invitation(value: unknown): Invitation {
  const body = record(value);
  return { id: text(body["id"]), role: text(body["role"]), expiresAt: text(body["expires_at"]) };
}

function issuedInvitation(value: unknown): IssuedInvitation {
  return { ...invitation(value), token: textOrNull(record(value)["token"]) };
}

function transfer(value: unknown): Transfer {
  const body = record(value);
  return {
    id: text(body["id"]),
    fromMembership: text(body["from_membership"]),
    toMembership: text(body["to_membership"]),
    status: text(body["status"]),
    expiresAt: text(body["expires_at"]),
  };
}

function pendingTransfer(value: unknown): Transfer | null {
  const pending = record(value)["pending"];
  return pending === null || pending === undefined ? null : transfer(pending);
}

function deletion(value: unknown): Deletion {
  const body = record(value);
  return { status: text(body["status"]), due: textOrNull(body["deletion_due"]) };
}

function activity(value: unknown): Activity {
  const body = record(value);
  return {
    id: text(body["id"]),
    at: text(body["at"]),
    actorKind: text(body["actor_kind"]),
    actorId: textOrNull(body["actor_id"]),
    action: text(body["action"]),
    subjectType: text(body["subject_type"]),
    subjectId: textOrNull(body["subject_id"]),
  };
}

function totals(value: unknown): Totals {
  const body = record(value);
  return {
    outstanding: whole(body["outstanding"]),
    debtors: whole(body["debtors"]),
    overdue: whole(body["overdue"]),
    dueToday: whole(body["due_today"]),
  };
}

function withDollars(value: unknown): Totals & { usd?: Totals } {
  const inDollars = record(value)["usd"];
  return { ...totals(value), ...(inDollars === undefined || inDollars === null ? {} : { usd: totals(inDollars) }) };
}

function ownerTotals(value: unknown): OwnerTotals {
  const body = record(value);
  return {
    items: list(body["items"], (element) => {
      const shop = record(element);
      return { ...withDollars(shop), shopId: text(shop["shop_id"]), name: text(shop["name"]) };
    }),
    total: withDollars(body["total"]),
  };
}

const segment = encodeURIComponent;

export function backoffice(api: ShopApi) {
  const { base, send } = api;
  const staff = `${base}/staff`;
  const transferPath = `${base}/ownership-transfer`;
  const deletionPath = `${base}/deletion`;
  return {
    listStaff(signal?: AbortSignal): Promise<Member[]> {
      return send({ method: "GET", path: staff, signal, read: reading.items(member) });
    },

    /** Creates an invitation for a manager or a seller; the answer carries its token once (REQ-032). */
    invite(role: "manager" | "seller", idempotencyKey: string): Promise<IssuedInvitation> {
      return send({ method: "POST", path: `${staff}/invitations`, body: { role }, idempotencyKey, read: issuedInvitation });
    },

    listInvitations(signal?: AbortSignal): Promise<Invitation[]> {
      return send({ method: "GET", path: `${staff}/invitations`, signal, read: reading.items(invitation) });
    },

    cancelInvitation(invitationId: string, idempotencyKey: string): Promise<void> {
      return send({
        method: "DELETE",
        path: `${staff}/invitations/${segment(invitationId)}`,
        idempotencyKey,
        read: () => undefined,
      });
    },

    /** Changes a member's role, or suspends or restores them (REQ-034). The owner's own is fixed. */
    updateMember(membershipId: string, patch: MemberPatch, idempotencyKey: string): Promise<Member> {
      return send({ method: "PATCH", path: `${staff}/${segment(membershipId)}`, body: patch, idempotencyKey, read: member });
    },

    removeMember(membershipId: string, idempotencyKey: string): Promise<Member> {
      return send({ method: "DELETE", path: `${staff}/${segment(membershipId)}`, idempotencyKey, read: member });
    },

    /** The offer that waits for an answer, if any. Managers and the owner may read it (REQ-036). */
    readTransfer(signal?: AbortSignal): Promise<Transfer | null> {
      return send({ method: "GET", path: transferPath, signal, read: pendingTransfer });
    },

    startTransfer(membershipId: string, idempotencyKey: string): Promise<Transfer> {
      return send({
        method: "POST",
        path: transferPath,
        body: { membership_id: membershipId },
        idempotencyKey,
        read: transfer,
      });
    },

    cancelTransfer(idempotencyKey: string): Promise<Transfer> {
      return send({ method: "DELETE", path: transferPath, idempotencyKey, read: transfer });
    },

    answerTransfer(answer: "accept" | "decline", idempotencyKey: string): Promise<Transfer> {
      return send({ method: "POST", path: `${transferPath}/${answer}`, idempotencyKey, read: transfer });
    },

    readDeletion(signal?: AbortSignal): Promise<Deletion> {
      return send({ method: "GET", path: deletionPath, signal, read: deletion });
    },

    /** Asks for the shop to be deleted; `confirmName` is the shop's name as the owner typed it (REQ-048). */
    requestDeletion(confirmName: string, idempotencyKey: string): Promise<Deletion> {
      return send({
        method: "POST",
        path: deletionPath,
        body: { confirm_name: confirmName },
        idempotencyKey,
        read: deletion,
      });
    },

    cancelDeletion(idempotencyKey: string): Promise<Deletion> {
      return send({ method: "DELETE", path: deletionPath, idempotencyKey, read: deletion });
    },

    /** The activity log, newest first, a page at a time (REQ-047). */
    listActivity(filter: ActivityFilter, signal?: AbortSignal): Promise<Page<Activity>> {
      return send({
        method: "GET",
        path: `${base}/activity`,
        query: { actor: filter.actor, action: filter.action, subject: filter.subject, cursor: filter.cursor },
        signal,
        read: reading.page(activity),
      });
    },

    /** Totals of every shop the caller owns, and their sum (REQ-065). */
    ownerTotals(signal?: AbortSignal): Promise<OwnerTotals> {
      return send({ method: "GET", path: "/api/v1/me/owner-totals", signal, read: ownerTotals });
    },
  };
}

export type Backoffice = ReturnType<typeof backoffice>;
