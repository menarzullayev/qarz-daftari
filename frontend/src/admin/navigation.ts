import type { MessageKey } from "../i18n/types";

/**
 * Navigation of the administration panel (REQ-058, REQ-059). It is deliberately its own model with its
 * own type: the admin panel is a separate entry point with separate authentication (ADR-011), and an
 * administrator has no shop role, so nothing here may come from the staff navigation.
 */
export type AdminNavItem = {
  id: string;
  path: string;
  labelKey: MessageKey;
};

export const ADMIN_SECTIONS: readonly AdminNavItem[] = [
  { id: "adminShops", path: "/", labelKey: "admin.nav.shops" },
  { id: "adminReceipts", path: "/receipts", labelKey: "admin.nav.receipts" },
  { id: "adminSettings", path: "/settings", labelKey: "admin.nav.settings" },
  { id: "adminSupportAccess", path: "/support-access", labelKey: "admin.nav.supportAccess" },
  { id: "adminAudit", path: "/audit", labelKey: "admin.nav.audit" },
];
