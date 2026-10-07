import { createContext, useContext, type ComponentType, type ReactNode } from "react";

import type { ApiError, CatalogItem, Customer, Debtor, ShopMembership } from "./api";

/**
 * The desktop renderings of the workspace's lists: real tables in place of the phone's rows (REQ-050,
 * REQ-N15). Only the web panel provides them, and only on a wide screen; everywhere else `useDesktop`
 * answers null and a screen draws the rows it always drew. The components themselves live in the
 * panel's entry, so none of their code is part of the Mini App's first load (NFR-010).
 */
export type Column<T> = {
  id: string;
  header: string;
  cell: (item: T) => ReactNode;
  /** Amounts and counts: right-aligned, in tabular figures. */
  numeric?: boolean;
  /** The cell that names the row; it is rendered as the row's header. */
  rowHeader?: boolean;
};

export type TableProps<T> = {
  /** Names the table for a screen reader; not drawn. */
  caption: string;
  columns: readonly Column<T>[];
  items: readonly T[];
  rowKey: (item: T) => string;
  /** Content that takes a full row under an item, such as an open form; null for none. */
  expanded?: (item: T) => ReactNode;
  /** One summary row: a cell per column. */
  foot?: readonly ReactNode[];
};

export type DesktopParts = {
  /** A real table for any list, for the screens that have no table of their own (the reports). */
  Table: <T>(props: TableProps<T>) => ReactNode;
  CustomersTable: ComponentType<{ items: readonly Customer[]; pick: boolean }>;
  DebtorsTable: ComponentType<{ items: readonly Debtor[] }>;
  CatalogTable: ComponentType<{
    items: readonly CatalogItem[];
    /** The controls of one item for a manager or an owner; null for a seller, who only reads. */
    controls: ((item: CatalogItem) => ReactNode) | null;
    /** The item whose form is open: its controls take a row of their own under it. */
    openId: string | null;
  }>;
};

const DesktopContext = createContext<DesktopParts | null>(null);

export const DesktopProvider = DesktopContext.Provider;

/** The desktop parts on a wide screen of the web panel; null on a phone and always in the Mini App. */
export function useDesktop(): DesktopParts | null {
  return useContext(DesktopContext);
}

/** What a control that switches the active shop needs (REQ-064); the logic stays in StaffRoot. */
export type ShopSwitch = {
  shops: readonly ShopMembership[];
  activeShopId: string;
  pending: boolean;
  error: ApiError | null;
  choose: (shop: ShopMembership) => void;
};

/**
 * What the web panel adds to the staff workspace. The Mini App passes none of it, so its screens, its
 * requests and its bundle stay as they are.
 */
export type WorkspaceExtension = {
  /** Wraps the shell inside the workspace context: state shared by the parts below. */
  Provider: ComponentType<{ children: ReactNode }>;
  /** Shown above every screen's content. */
  Banner: ComponentType;
  /** Screens of navigation sections that have none in the shared workspace, by section id. */
  sections: Readonly<Record<string, ComponentType | undefined>>;
  /** Under the shop's settings. */
  SettingsExtra: ComponentType;
  /** Under the overview. */
  OverviewExtra: ComponentType;
};
