import { type ReactNode, useSyncExternalStore } from "react";

import { useI18n } from "../i18n/I18nProvider";
import type { CatalogItem, Customer, Debtor } from "../shared/api";
import { formatMoney } from "../shared/format";
import { type DesktopParts, DesktopProvider } from "../shared/layout";
import { Link } from "../shared/router";
import { owesAnything } from "../shared/workspace/CustomersScreen";
import { Money } from "../shared/workspace/parts";
import { type Column, DataTable } from "./DataTable";
import "./messages";

/** From this width the panel draws its lists as tables; under it, the phone's rows (REQ-050, REQ-N15). */
export const DESKTOP_QUERY = "(min-width: 1024px)";

const NONE = "—";

function CustomersTable({ items, pick }: { items: readonly Customer[]; pick: boolean }) {
  const { t } = useI18n();
  const columns: Column<Customer>[] = [
    {
      id: "customer",
      header: t("table.customer"),
      rowHeader: true,
      // Picking a customer for a new entry offers the two entries; the name is a link only in the book.
      cell: (customer) => (pick ? customer.displayName : <Link to={`/customers/${customer.id}`}>{customer.displayName}</Link>),
    },
    { id: "phone", header: t("table.phone"), cell: (customer) => customer.phone ?? NONE },
    {
      id: "balance",
      header: t("table.balance"),
      numeric: true,
      cell: (customer) => <Money uzs={customer.balance} usd={customer.usd?.balance} />,
    },
  ];
  if (pick) {
    columns.push({
      id: "actions",
      header: t("table.actions"),
      cell: (customer) => (
        <span className="table__actions">
          <Link to={`/customers/${customer.id}/credit`} className="button button--primary button--small">
            {t("entry.credit.short")}
          </Link>
          {/* A payment cannot exceed the debt, so there is nothing to pay when nothing is owed. */}
          {owesAnything(customer) ? (
            <Link to={`/customers/${customer.id}/payment`} className="button button--small">
              {t("entry.payment.short")}
            </Link>
          ) : null}
        </span>
      ),
    });
  }
  return <DataTable caption={t("table.customers")} columns={columns} items={items} rowKey={(customer) => customer.id} />;
}

function DebtorsTable({ items }: { items: readonly Debtor[] }) {
  const { t, language } = useI18n();
  // A figure of nothing is a dash. With dollars each figure has its dollar twin under it, never added.
  const money = (amount: number, usd?: number) =>
    usd === undefined ? amount > 0 ? formatMoney(amount, language) : NONE : <Money uzs={amount} usd={usd} />;
  const columns: Column<Debtor>[] = [
    {
      id: "customer",
      header: t("table.customer"),
      rowHeader: true,
      cell: (debtor) => <Link to={`/customers/${debtor.id}`}>{debtor.displayName}</Link>,
    },
    {
      id: "balance",
      header: t("table.balance"),
      numeric: true,
      cell: (debtor) => <Money uzs={debtor.balance} usd={debtor.usd?.balance} />,
    },
    {
      id: "overdue",
      header: t("table.overdue"),
      numeric: true,
      cell: (debtor) => money(debtor.overdue.amount, debtor.usd?.overdue?.amount),
    },
    {
      id: "late",
      header: t("table.late"),
      cell: (debtor) =>
        debtor.overdue.amount > 0 && debtor.overdue.days > 0 ? t("overdue.days", { count: debtor.overdue.days }) : NONE,
    },
    {
      id: "dueToday",
      header: t("table.dueToday"),
      numeric: true,
      cell: (debtor) => money(debtor.overdue.dueToday, debtor.usd?.overdue?.dueToday),
    },
  ];
  return <DataTable caption={t("overview.debtors")} columns={columns} items={items} rowKey={(debtor) => debtor.id} />;
}

function CatalogTable({
  items,
  controls,
  openId,
}: {
  items: readonly CatalogItem[];
  controls: ((item: CatalogItem) => ReactNode) | null;
  openId: string | null;
}) {
  const { t, language } = useI18n();
  const columns: Column<CatalogItem>[] = [
    { id: "item", header: t("table.item"), rowHeader: true, cell: (item) => item.name },
    { id: "unit", header: t("table.unit"), cell: (item) => item.unit },
    { id: "price", header: t("table.price"), numeric: true, cell: (item) => formatMoney(item.price, language) },
    {
      id: "note",
      header: t("table.note"),
      cell: (item) =>
        item.learned ? (
          <strong>{t("catalog.learned")}</strong>
        ) : item.mergedInto !== null ? (
          t("catalog.merged")
        ) : (
          NONE
        ),
    },
  ];
  if (controls) {
    columns.push({
      id: "actions",
      header: t("table.actions"),
      // An open form is too wide for a cell: it takes the row under the item instead.
      cell: (item) => (item.id === openId ? null : controls(item)),
    });
  }
  return (
    <DataTable
      caption={t("table.catalog")}
      columns={columns}
      items={items}
      rowKey={(item) => item.id}
      expanded={(item) => (controls && item.id === openId ? controls(item) : null)}
    />
  );
}

const PARTS: DesktopParts = { Table: DataTable, CustomersTable, DebtorsTable, CatalogTable };

function subscribe(onChange: () => void): () => void {
  if (typeof window.matchMedia !== "function") {
    return () => undefined;
  }
  const query = window.matchMedia(DESKTOP_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function isWide(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(DESKTOP_QUERY).matches;
}

/**
 * Gives the screens inside it the desktop tables while the window is wide, and takes them away when
 * it is not: under the breakpoint the panel is the phone layout the Mini App has.
 */
export function DesktopLayout({ children }: { children: ReactNode }) {
  const wide = useSyncExternalStore(subscribe, isWide, () => false);
  return <DesktopProvider value={wide ? PARTS : null}>{children}</DesktopProvider>;
}
