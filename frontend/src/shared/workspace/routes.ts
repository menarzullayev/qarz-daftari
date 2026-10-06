import type { MessageKey } from "../../i18n/types";
import type { EntryKind } from "../api";

export type WorkspaceRoute =
  | { screen: "overview" }
  | { screen: "customers" }
  | { screen: "pickCustomer" }
  | { screen: "newCustomer" }
  | { screen: "customer"; customerId: string }
  | { screen: "entry"; customerId: string; kind: EntryKind }
  | { screen: "addGoods"; customerId: string; entryId: string }
  | { screen: "catalog" }
  | { screen: "shopSettings" };

export type WorkspaceMatch = {
  route: WorkspaceRoute;
  /** Path of the navigation section the screen belongs to; it decides who may open it (REQ-033). */
  sectionPath: string;
  titleKey: MessageKey;
};

// Identifiers are UUIDs. Anything else in their place is an unknown route, not a request to the server.
const ID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const CUSTOMER = new RegExp(`^/customers/(${ID})$`, "i");
const ENTRY = new RegExp(`^/customers/(${ID})/(credit|payment)$`, "i");
const ADD_GOODS = new RegExp(`^/customers/(${ID})/entries/(${ID})/goods$`, "i");

/** The data screen for a route path, or null when the path is not one of them. */
export function matchWorkspaceRoute(path: string): WorkspaceMatch | null {
  switch (path) {
    case "/":
      return { route: { screen: "overview" }, sectionPath: "/", titleKey: "nav.overview" };
    case "/customers":
      return { route: { screen: "customers" }, sectionPath: "/customers", titleKey: "nav.customers" };
    case "/customers/new":
      return { route: { screen: "newCustomer" }, sectionPath: "/customers", titleKey: "customers.add" };
    case "/new":
      return { route: { screen: "pickCustomer" }, sectionPath: "/new", titleKey: "nav.newEntry" };
    case "/catalog":
      return { route: { screen: "catalog" }, sectionPath: "/catalog", titleKey: "nav.catalog" };
    case "/shop-settings":
      return { route: { screen: "shopSettings" }, sectionPath: "/shop-settings", titleKey: "nav.shopSettings" };
  }
  const customer = CUSTOMER.exec(path);
  if (customer?.[1]) {
    return {
      route: { screen: "customer", customerId: customer[1] },
      sectionPath: "/customers",
      titleKey: "customer.title",
    };
  }
  const entry = ENTRY.exec(path);
  if (entry?.[1]) {
    const kind: EntryKind = entry[2]?.toLowerCase() === "payment" ? "payment" : "credit";
    return {
      route: { screen: "entry", customerId: entry[1], kind },
      sectionPath: "/customers",
      titleKey: kind === "payment" ? "entry.payment.title" : "entry.credit.title",
    };
  }
  const goods = ADD_GOODS.exec(path);
  if (goods?.[1] && goods[2]) {
    return {
      route: { screen: "addGoods", customerId: goods[1], entryId: goods[2] },
      sectionPath: "/customers",
      titleKey: "goods.later.title",
    };
  }
  return null;
}
