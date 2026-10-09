import { type Features, isRole, type Role } from "./navigation";
import type { Held } from "./permissions";

/**
 * What the shell needs to know about the signed-in staff member. StaffRoot builds it from the sign-in
 * and the user's shops (GET /api/v1/me/shops); without a session the "sign-in required" screen is shown.
 */
export type StaffSession = {
  /** Name of the active shop; every screen shows it (REQ-064). */
  shopName: string;
  role: Role;
  /** The person's membership in the active shop, when the server says which it is. */
  membershipId?: string | null;
  /** What the server said the person may do in the active shop; absent or null when it keeps to roles. */
  permissions?: Held | undefined;
  /** The parts of the product the platform has switched on; absent: none of them. */
  features?: Features | undefined;
};

/**
 * Development aid only: `?role=owner&shop=Name` lets a developer look at the shell for a role while
 * there is no sign-in yet. Callers must guard it with `import.meta.env.DEV`, so the code is removed
 * from production builds; it grants nothing, because the server decides what a role may do.
 */
export function previewStaffSession(search: string): StaffSession | null {
  const params = new URLSearchParams(search);
  const role = params.get("role");
  const shopName = params.get("shop")?.trim();
  return isRole(role) && shopName ? { role, shopName } : null;
}

/** Development aid only, as above: `?admin=1` shows the administration navigation. */
export function previewAdminSession(search: string): boolean {
  return new URLSearchParams(search).get("admin") === "1";
}
