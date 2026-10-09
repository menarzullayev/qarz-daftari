import type { Role } from "./navigation";

/**
 * What a member of a shop may do, one permission at a time (the server's catalogue:
 * backend/src/qarz/domain/permissions.py). The interface uses this only to decide what to offer; the
 * server checks every call and is the authority.
 *
 * While the platform's permission switch is on, the server says what the signed-in member holds
 * (GET /shops/{id}/permissions/mine) and that list decides. While it is off the route does not exist,
 * nothing is known beyond the role, and the role decides as it always did: `MIN_ROLE` is the lowest role
 * that holds each permission by default. A backend test (tests/test_permissions.py) keeps this table
 * equal to the catalogue's defaults.
 */
export const MIN_ROLE = {
  "ledger.view": "seller",
  "customers.create": "seller",
  "customers.edit": "manager",
  "customers.share": "manager",
  "credits.record": "seller",
  "payments.record": "seller",
  "payment_notices.decide": "seller",
  "entries.others": "manager",
  "entries.over_limit": "manager",
  "entries.cancel": "manager",
  "promises.change": "manager",
  "disputes.decide": "manager",
  "goods.edit": "manager",
  "stock.view": "seller",
  "stock.receive": "manager",
  "stock.adjust": "manager",
  "stock.costs.view": "manager",
  "suppliers.view": "manager",
  "suppliers.manage": "manager",
  "suppliers.pay": "manager",
  "reminders.send": "manager",
  "reports.view": "manager",
  "reports.export": "manager",
  "imports.run": "manager",
  "cash.view": "manager",
  "cash.record_income": "manager",
  "cash.record_expense": "manager",
  "cash.cancel": "manager",
  "cash.categories": "manager",
  "cash.backfill": "owner",
  "settings.view": "manager",
  "settings.edit": "manager",
  "shop.edit": "owner",
  "staff.manage": "owner",
  "activity.view": "owner",
  "membership.own": "seller",
  "permissions.manage": "owner",
  "ownership.transfer": "owner",
  "ownership.receive": "manager",
  "subscription.manage": "owner",
  "support.manage": "owner",
  "shop.delete": "owner",
} as const satisfies Record<string, Role>;

export type PermissionKey = keyof typeof MIN_ROLE;

/** The permissions the server said the member holds, or null when it keeps to roles. */
export type Held = ReadonlySet<string> | null;

export type Viewer = { role: Role; permissions?: Held | undefined };

const RANK: Readonly<Record<Role, number>> = { seller: 0, manager: 1, owner: 2 };

export function may(viewer: Viewer, permission: PermissionKey): boolean {
  if (viewer.permissions) {
    return viewer.permissions.has(permission);
  }
  return RANK[viewer.role] >= RANK[MIN_ROLE[permission]];
}

export function mayAny(viewer: Viewer, permissions: readonly PermissionKey[]): boolean {
  return permissions.some((permission) => may(viewer, permission));
}
