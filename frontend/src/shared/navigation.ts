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

/** A section opens for whoever holds any one of `needs`. */
type StaffSection = NavItem & { needs: readonly PermissionKey[] };

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
 * The sections of the stock (the expansion's module I). They exist only while the platform switch
 * `stock_on` is on, which the server says by answering the stock's settings; until then, and for a
 * client that was not told, none of them is offered. `office` marks the one the web panel alone has:
 * the documents are heavy tables, the Mini App keeps to the counter's tasks.
 */
const STOCK_SECTIONS: readonly (StaffSection & { office?: true })[] = [
  { id: "stock", path: "/stock", labelKey: "nav.stock", needs: ["stock.view"] },
  {
    id: "stockDocuments",
    path: "/stock-documents",
    labelKey: "nav.stockDocuments",
    needs: ["stock.receive", "stock.adjust"],
    office: true,
  },
  { id: "suppliers", path: "/suppliers", labelKey: "nav.suppliers", needs: ["suppliers.view"] },
];

export const STOCK_SECTION_IDS: readonly string[] = STOCK_SECTIONS.map((section) => section.id);

/** The section the stock's own follow: goods are in the catalog, the stock counts them. */
const STOCK_AFTER = "catalog";

/** What the server said exists beyond the base workspace, and which client is asking. */
export type Features = {
  /** The stock is switched on for the platform and the member may see it. */
  stock?: boolean | undefined;
  /** The web panel, which has the screens made of tables. */
  office?: boolean | undefined;
};

/**
 * Exactly the sections the member may open, in display order: by what the server said they hold, or by
 * the role alone when it said nothing (`permissions` absent or null). The stock's sections join them
 * only when `features` says the stock is on.
 */
export function staffSections(role: Role, permissions?: Held, features: Features = {}): NavItem[] {
  const all: StaffSection[] = [];
  for (const section of STAFF_SECTIONS) {
    all.push(section);
    if (section.id === STOCK_AFTER && features.stock === true) {
      all.push(...STOCK_SECTIONS.filter((added) => added.office !== true || features.office === true));
    }
  }
  return all
    .filter((section) => mayAny({ role, permissions }, section.needs))
    .map(({ id, path, labelKey }) => ({ id, path, labelKey }));
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
