import { useI18n } from "../i18n/I18nProvider";
import { formatCustomerCount } from "../shared/format";
import { useLoad } from "../shared/hooks";
import { useWorkspace } from "../shared/workspace/context";
import { Empty, Failure, Loading, Money } from "../shared/workspace/parts";
import type { ShopTotals, Totals } from "./backoffice";
import { type Column, DataTable } from "./DataTable";
import "./messages";
import { useOffice } from "./office";

function AllShops() {
  const { office } = useOffice();
  const { t, language } = useI18n();
  const { state, reload } = useLoad((signal) => office.ownerTotals(signal), [office]);

  let body = <Loading />;
  if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.status === "ready") {
    const { items, total } = state.data;
    // A shop that works in dollars has a dollar figure under each so'm one; the two are never added.
    type Row = Totals & { usd?: Totals };
    const money = (row: Row, figure: "outstanding" | "overdue" | "dueToday") => (
      <Money uzs={row[figure]} usd={row.usd?.[figure]} />
    );
    // Who owes dollars is counted apart from who owes so'm, and is marked as the dollar count.
    const debtors = (row: Row) =>
      row.usd === undefined ? (
        formatCustomerCount(row.debtors, language)
      ) : (
        <>
          <span className="money">{formatCustomerCount(row.debtors, language)}</span>{" "}
          <span className="money">
            {t("currency.usd")} {formatCustomerCount(row.usd.debtors, language)}
          </span>
        </>
      );
    const columns: Column<ShopTotals>[] = [
      { id: "shop", header: t("totals.shop"), rowHeader: true, cell: (shop) => shop.name },
      { id: "outstanding", header: t("overview.outstanding"), numeric: true, cell: (shop) => money(shop, "outstanding") },
      { id: "debtors", header: t("overview.debtors"), numeric: true, cell: debtors },
      { id: "overdue", header: t("overview.overdue"), numeric: true, cell: (shop) => money(shop, "overdue") },
      { id: "dueToday", header: t("overview.dueToday"), numeric: true, cell: (shop) => money(shop, "dueToday") },
    ];
    body = items.length === 0 ? (
      <Empty>{t("totals.none")}</Empty>
    ) : (
      <DataTable
        caption={t("totals.title")}
        columns={columns}
        items={items}
        rowKey={(shop) => shop.shopId}
        foot={[
          t("totals.total"),
          money(total, "outstanding"),
          debtors(total),
          money(total, "overdue"),
          money(total, "dueToday"),
        ]}
      />
    );
  }

  return (
    <section aria-labelledby="totals-title">
      <h2 id="totals-title">{t("totals.title")}</h2>
      <p className="hint">{t("totals.hint")}</p>
      {body}
    </section>
  );
}

/**
 * What every shop a person owns is owed, shop by shop and in total (REQ-065). Shown, and asked of the
 * server, only for someone who owns more than one shop: with one, the overview above says it all.
 */
export function OwnerTotals() {
  const { shops } = useWorkspace();
  const owned = (shops ?? []).filter((shop) => shop.role === "owner").length;
  return owned > 1 ? <AllShops /> : null;
}
