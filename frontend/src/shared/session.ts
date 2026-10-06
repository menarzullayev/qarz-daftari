import { isRole, type Role } from "./navigation";

/**
 * What the shell needs to know about the signed-in staff member. It will come from the authentication
 * call (POST /api/v1/auth/telegram-webapp or /telegram-login) in a later story. Until then a production
 * build has no session and shows the "sign-in required" screen.
 */
export type StaffSession = {
  /** Name of the active shop; every screen shows it (REQ-064). */
  shopName: string;
  role: Role;
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
