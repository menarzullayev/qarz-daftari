import type { MessageKey } from "../i18n/types";
import { type Held, mayAny, type PermissionKey } from "./permissions";

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

/**
 * Whether the role may correct the book: reverse an entry, rename or archive a customer. The interface
 * uses it to decide what to offer; the server refuses the call from a seller whatever the interface shows.
 */
export function canManage(role: Role): boolean {
  return RANK[role] >= RANK.manager;
}

/**
 * A part of the product that exists only while the platform has switched it on. The server says which
 * are on (a header of the person's shops); a section of one that is off is offered to nobody.
 */
export type Feature = "cashBook";
export type Features = Readonly<Partial<Record<Feature, boolean>>>;

/** A section opens for whoever holds any one of `needs`, and only while its `feature`, if any, is on. */
type StaffSection = NavItem & { needs: readonly PermissionKey[]; feature?: Feature };

/**
 * Sections of the staff workspace and the permissions that open each (REQ-033; the server's catalogue).
 * By role alone a manager can do everything a seller can and an owner everything; the owner may then
 * change that for one member, and the server says what the member holds. This list only decides what
 * the interface offers: the server checks every call and is the authority.
 */
const STAFF_SECTIONS: readonly StaffSection[] = [
  { id: "overview", path: "/", labelKey: "nav.overview", needs: ["ledger.view"] },
  { id: "customers", path: "/customers", labelKey: "nav.customers", needs: ["ledger.view"] },
  { id: "newEntry", path: "/new", labelKey: "nav.newEntry", needs: ["credits.record", "payments.record"] },
  // A seller reads the catalog to pick goods; only a manager or an owner changes it (REQ-039).
  { id: "catalog", path: "/catalog", labelKey: "nav.catalog", needs: ["ledger.view"] },
  { id: "reminders", path: "/reminders", labelKey: "nav.reminders", needs: ["reminders.send", "settings.view"] },
  { id: "reports", path: "/reports", labelKey: "nav.reports", needs: ["reports.view"] },
  // The cash book (expansion module H), behind the platform switch `cash_book_on`: for whoever may read
  // it, record in it or arrange its categories.
  {
    id: "cash",
    path: "/cash",
    labelKey: "nav.cash",
    needs: ["cash.view", "cash.record_income", "cash.record_expense", "cash.categories"],
    feature: "cashBook",
  },
  { id: "disputes", path: "/disputes", labelKey: "nav.disputes", needs: ["disputes.decide"] },
  { id: "importExport", path: "/import-export", labelKey: "nav.importExport", needs: ["imports.run", "reports.export"] },
  { id: "staff", path: "/staff", labelKey: "nav.staff", needs: ["staff.manage"] },
  { id: "activityLog", path: "/activity", labelKey: "nav.activityLog", needs: ["activity.view"] },
  { id: "subscription", path: "/subscription", labelKey: "nav.subscription", needs: ["subscription.manage"] },
  // A manager reads the settings; only the owner changes them (the server: settings.view, shop.edit).
  { id: "shopSettings", path: "/shop-settings", labelKey: "nav.shopSettings", needs: ["settings.view"] },
];

export const STAFF_SECTION_IDS: readonly string[] = STAFF_SECTIONS.map((section) => section.id);

/**
 * Exactly the sections the member may open, in display order: by what the server said they hold, or by
 * the role alone when it said nothing (`permissions` absent or null).
 */
export function staffSections(role: Role, permissions?: Held, features: Features = {}): NavItem[] {
  return STAFF_SECTIONS.filter(
    (section) =>
      (section.feature === undefined || features[section.feature] === true) &&
      mayAny({ role, permissions }, section.needs),
  ).map(({ id, path, labelKey }) => ({ id, path, labelKey }));
}

/** How many sections fit in the bottom tab bar of a phone; the rest sit behind "More". */
export const PRIMARY_TAB_COUNT = 3;

/**
 * How many of a role's sections the phone tab bar shows. A "More" tab that would hold a single section
 * is replaced by that section: the bar has four tabs either way, and a seller reaches the catalog in one tap.
 */
export function primaryTabCount(sections: readonly NavItem[]): number {
  return sections.length <= PRIMARY_TAB_COUNT + 1 ? sections.length : PRIMARY_TAB_COUNT;
}

/** Screen that lists the sections which do not fit in the bottom tab bar. */
export const MORE_ITEM: NavItem = { id: "more", path: "/more", labelKey: "nav.more" };
