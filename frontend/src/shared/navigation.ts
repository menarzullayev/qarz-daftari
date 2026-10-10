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
export type Feature = "cashBook" | "stock" | "network" | "catalog" | "address";
export type Features = Readonly<Partial<Record<Feature, boolean>>>;

/** A section opens for whoever holds any one of `needs`, and only while its `feature`, if any, is on. */
type StaffSection = NavItem & { needs: readonly PermissionKey[]; feature?: Feature };

/**
 * A section of the stock. `office` marks the one the web panel alone has. `counter` is what opens the
 * section where that one is absent (the Mini App), when it is more than `needs`.
 */
type StockSection = StaffSection & { office?: true; counter?: readonly PermissionKey[] };

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
 * The sections of the stock (the expansion's module I). They exist only while the platform switch
 * `stock_on` is on, which the server says with a header of the person's shops; until then, and for a
 * client that was not told, none of them is offered. `office` marks the one the web panel alone has:
 * the documents are heavy tables, the Mini App keeps to the counter's tasks.
 *
 * In the Mini App the documents are reached through the stock's own section (`#/stock/documents`), so
 * there the section opens for whoever holds any permission of the stock, and each of its screens then
 * shows what that member may read: a member who writes documents but may not see the stock gets the
 * documents and not the items. In the panel the two are sections of their own, each by its permission.
 */
const STOCK_SECTIONS: readonly StockSection[] = [
  {
    id: "stock",
    path: "/stock",
    labelKey: "nav.stock",
    needs: ["stock.view"],
    counter: ["stock.view", "stock.receive", "stock.adjust"],
    feature: "stock",
  },
  {
    id: "stockDocuments",
    path: "/stock-documents",
    labelKey: "nav.stockDocuments",
    needs: ["stock.receive", "stock.adjust"],
    feature: "stock",
    office: true,
  },
  { id: "suppliers", path: "/suppliers", labelKey: "nav.suppliers", needs: ["suppliers.view"], feature: "stock" },
];

export const STOCK_SECTION_IDS: readonly string[] = STOCK_SECTIONS.map((section) => section.id);

/**
 * The network between shops (the expansion's module J): two shops linked as buyer and supplier. One
 * section, behind the platform switch `network_on`, which the server says with a header of the
 * person's shops as it does for the stock; it follows the stock's sections.
 */
const NETWORK_SECTIONS: readonly StaffSection[] = [
  { id: "network", path: "/network", labelKey: "nav.network", needs: ["network.view"], feature: "network" },
];

export const NETWORK_SECTION_IDS: readonly string[] = NETWORK_SECTIONS.map((section) => section.id);

/** The section the stock's own follow: goods are in the catalog, the stock counts them. */
const STOCK_AFTER = "catalog";

/**
 * Exactly the sections the member may open, in display order: by what the server said they hold, or by
 * the role alone when it said nothing (`permissions` absent or null). A section of a part of the
 * product that the platform has not switched on is offered to nobody. `office` is true for the web
 * panel, which has the screens made of tables.
 */
export function staffSections(role: Role, permissions?: Held, features: Features = {}, office = false): NavItem[] {
  const all: StaffSection[] = [];
  for (const section of STAFF_SECTIONS) {
    all.push(section);
    if (section.id === STOCK_AFTER) {
      for (const added of STOCK_SECTIONS) {
        if (added.office !== true || office) {
          all.push(office || added.counter === undefined ? added : { ...added, needs: added.counter });
        }
      }
      all.push(...NETWORK_SECTIONS);
    }
  }
  return all
    .filter(
      (section) =>
        (section.feature === undefined || features[section.feature] === true) &&
        mayAny({ role, permissions }, section.needs),
    )
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
