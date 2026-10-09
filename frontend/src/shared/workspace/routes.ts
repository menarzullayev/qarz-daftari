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
  | { screen: "waiting" }
  | { screen: "counterCode" }
  | { screen: "disputes" }
  | { screen: "paymentNotices" }
  | { screen: "dateRequests" }
  | { screen: "reports" }
  | { screen: "cash" }
  | { screen: "exports" }
  | { screen: "catalog" }
  | { screen: "reminders" }
  | { screen: "subscription" }
  | { screen: "shopSettings" }
  | { screen: "stock"; view: StockView }
  | { screen: "network"; view: NetworkView };

/** The screens of the network between shops; their code is loaded apart (module J). */
export type NetworkView =
  | { name: "home" }
  | { name: "link"; linkId: string }
  | { name: "orders"; role: "buyer" | "supplier" }
  | { name: "compose"; draftId: string | null }
  | { name: "order"; orderId: string }
  | { name: "notes"; waiting: boolean }
  | { name: "note"; noteId: string }
  | { name: "payments" };

/** The screens of the stock, its documents and the suppliers; their code is loaded apart (module I). */
export type StockView =
  | { name: "items" }
  | { name: "item"; itemId: string }
  | { name: "receipt" }
  | { name: "report" }
  | { name: "documents" }
  | { name: "newDocument"; kind: StockDocumentKind }
  | { name: "document"; documentId: string }
  | { name: "suppliers" }
  | { name: "supplier"; supplierId: string };

export const STOCK_DOCUMENT_KINDS = ["receipt", "customer_return", "supplier_return", "write_off", "stocktake"] as const;
export type StockDocumentKind = (typeof STOCK_DOCUMENT_KINDS)[number];

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
const STOCK_ITEM = new RegExp(`^/stock/items/(${ID})$`, "i");
const STOCK_DOCUMENT = new RegExp(`^/stock-documents/(${ID})$`, "i");
const NEW_STOCK_DOCUMENT = /^\/stock-documents\/new\/([a-z_]+)$/;
const SUPPLIER = new RegExp(`^/suppliers/(${ID})$`, "i");
const NETWORK_LINK = new RegExp(`^/network/links/(${ID})$`, "i");
const NETWORK_DRAFT = new RegExp(`^/network/drafts/(${ID})$`, "i");
const NETWORK_ORDER = new RegExp(`^/network/orders/(${ID})$`, "i");
const NETWORK_NOTE = new RegExp(`^/network/notes/(${ID})$`, "i");
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
    // Connecting customers is part of the customer book, so the phone's tab bar stays as it is.
    case "/customers/waiting":
      return { route: { screen: "waiting" }, sectionPath: "/customers", titleKey: "waiting.title" };
    case "/customers/counter-code":
      return { route: { screen: "counterCode" }, sectionPath: "/customers", titleKey: "counter.title" };
    case "/disputes":
      return { route: { screen: "disputes" }, sectionPath: "/disputes", titleKey: "disputes.title" };
    // Every role decides payment notices, so they sit with the customer book, which every role has.
    case "/payment-notices":
      return { route: { screen: "paymentNotices" }, sectionPath: "/customers", titleKey: "notices.title" };
    // Requests to move a date sit with the disputes: both are what customers ask and managers answer.
    case "/date-requests":
      return { route: { screen: "dateRequests" }, sectionPath: "/disputes", titleKey: "dates.title" };
    case "/reports":
      return { route: { screen: "reports" }, sectionPath: "/reports", titleKey: "nav.reports" };
    case "/cash":
      return { route: { screen: "cash" }, sectionPath: "/cash", titleKey: "nav.cash" };
    // Export is ready; import, the other half of the section, is not, and the screen says so.
    case "/import-export":
      return { route: { screen: "exports" }, sectionPath: "/import-export", titleKey: "nav.importExport" };
    case "/new":
      return { route: { screen: "pickCustomer" }, sectionPath: "/new", titleKey: "nav.newEntry" };
    case "/catalog":
      return { route: { screen: "catalog" }, sectionPath: "/catalog", titleKey: "nav.catalog" };
    case "/reminders":
      return { route: { screen: "reminders" }, sectionPath: "/reminders", titleKey: "nav.reminders" };
    case "/subscription":
      return { route: { screen: "subscription" }, sectionPath: "/subscription", titleKey: "nav.subscription" };
    case "/shop-settings":
      return { route: { screen: "shopSettings" }, sectionPath: "/shop-settings", titleKey: "nav.shopSettings" };
  }
  const stock = matchStockRoute(path);
  if (stock) {
    return stock;
  }
  const network = matchNetworkRoute(path);
  if (network) {
    return network;
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

/**
 * The stock's screens. A path is matched whether or not the stock is switched on: who may open it is
 * decided by the navigation section it belongs to, which is absent while the stock is off.
 */
function matchStockRoute(path: string): WorkspaceMatch | null {
  const stock = (view: StockView, sectionPath: string, titleKey: MessageKey): WorkspaceMatch => ({
    route: { screen: "stock", view },
    sectionPath,
    titleKey,
  });
  switch (path) {
    case "/stock":
      return stock({ name: "items" }, "/stock", "nav.stock");
    case "/stock/receipt":
      return stock({ name: "receipt" }, "/stock", "nav.stock");
    case "/stock/report":
      return stock({ name: "report" }, "/stock", "nav.stock");
    case "/stock-documents":
      return stock({ name: "documents" }, "/stock-documents", "nav.stockDocuments");
    case "/suppliers":
      return stock({ name: "suppliers" }, "/suppliers", "nav.suppliers");
  }
  const item = STOCK_ITEM.exec(path);
  if (item?.[1]) {
    return stock({ name: "item", itemId: item[1] }, "/stock", "nav.stock");
  }
  const document = STOCK_DOCUMENT.exec(path);
  if (document?.[1]) {
    return stock({ name: "document", documentId: document[1] }, "/stock-documents", "nav.stockDocuments");
  }
  const kind = STOCK_DOCUMENT_KINDS.find((known) => known === NEW_STOCK_DOCUMENT.exec(path)?.[1]);
  if (kind) {
    return stock({ name: "newDocument", kind }, "/stock-documents", "nav.stockDocuments");
  }
  const supplier = SUPPLIER.exec(path);
  if (supplier?.[1]) {
    return stock({ name: "supplier", supplierId: supplier[1] }, "/suppliers", "nav.suppliers");
  }
  return null;
}

/**
 * The screens of the network between shops. As with the stock, a path is matched whether or not the
 * network is switched on: the one section they all belong to is absent while it is off.
 */
function matchNetworkRoute(path: string): WorkspaceMatch | null {
  const network = (view: NetworkView): WorkspaceMatch => ({
    route: { screen: "network", view },
    sectionPath: "/network",
    titleKey: "nav.network",
  });
  switch (path) {
    case "/network":
      return network({ name: "home" });
    case "/network/orders/out":
      return network({ name: "orders", role: "buyer" });
    case "/network/orders/in":
      return network({ name: "orders", role: "supplier" });
    case "/network/orders/new":
      return network({ name: "compose", draftId: null });
    case "/network/notes":
      return network({ name: "notes", waiting: false });
    case "/network/notes/waiting":
      return network({ name: "notes", waiting: true });
    case "/network/payments":
      return network({ name: "payments" });
  }
  const link = NETWORK_LINK.exec(path);
  if (link?.[1]) {
    return network({ name: "link", linkId: link[1] });
  }
  const draft = NETWORK_DRAFT.exec(path);
  if (draft?.[1]) {
    return network({ name: "compose", draftId: draft[1] });
  }
  const order = NETWORK_ORDER.exec(path);
  if (order?.[1]) {
    return network({ name: "order", orderId: order[1] });
  }
  const note = NETWORK_NOTE.exec(path);
  if (note?.[1]) {
    return network({ name: "note", noteId: note[1] });
  }
  return null;
}
