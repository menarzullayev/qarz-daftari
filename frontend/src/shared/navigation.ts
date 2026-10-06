import type { MessageKey } from "../i18n/types";

/** One destination in a navigation list. The label is a catalog key, never text. */
export type NavItem = {
  id: string;
  /** Route path, starting with "/". */
  path: string;
  labelKey: MessageKey;
};

export const ROLES = ["seller", "manager", "owner"] as const;
export type Role = (typeof ROLES)[number];

export function isRole(value: unknown): value is Role {
  return ROLES.some((role) => role === value);
}

const RANK: Readonly<Record<Role, number>> = { seller: 0, manager: 1, owner: 2 };

type StaffSection = NavItem & { minRole: Role };

/**
 * Sections of the staff workspace and the lowest role that may open each (REQ-033). A manager can do
 * everything a seller can; an owner can do everything. This list only decides what the interface
 * offers: the server checks the role on every call and is the authority.
 */
const STAFF_SECTIONS: readonly StaffSection[] = [
  { id: "overview", path: "/", labelKey: "nav.overview", minRole: "seller" },
  { id: "customers", path: "/customers", labelKey: "nav.customers", minRole: "seller" },
  { id: "newEntry", path: "/new", labelKey: "nav.newEntry", minRole: "seller" },
  { id: "catalog", path: "/catalog", labelKey: "nav.catalog", minRole: "manager" },
  { id: "reminders", path: "/reminders", labelKey: "nav.reminders", minRole: "manager" },
  { id: "reports", path: "/reports", labelKey: "nav.reports", minRole: "manager" },
  { id: "disputes", path: "/disputes", labelKey: "nav.disputes", minRole: "manager" },
  { id: "importExport", path: "/import-export", labelKey: "nav.importExport", minRole: "manager" },
  { id: "staff", path: "/staff", labelKey: "nav.staff", minRole: "owner" },
  { id: "activityLog", path: "/activity", labelKey: "nav.activityLog", minRole: "owner" },
  { id: "subscription", path: "/subscription", labelKey: "nav.subscription", minRole: "owner" },
  { id: "shopSettings", path: "/shop-settings", labelKey: "nav.shopSettings", minRole: "owner" },
];

export const STAFF_SECTION_IDS: readonly string[] = STAFF_SECTIONS.map((section) => section.id);

/** Exactly the sections the role may open, in display order. */
export function staffSections(role: Role): NavItem[] {
  return STAFF_SECTIONS.filter((section) => RANK[role] >= RANK[section.minRole]).map(
    ({ id, path, labelKey }) => ({ id, path, labelKey }),
  );
}

/** How many sections fit in the bottom tab bar of a phone; the rest sit behind "More". */
export const PRIMARY_TAB_COUNT = 3;

/** Screen that lists the sections which do not fit in the bottom tab bar. */
export const MORE_ITEM: NavItem = { id: "more", path: "/more", labelKey: "nav.more" };
