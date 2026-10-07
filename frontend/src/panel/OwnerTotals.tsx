import { useI18n } from "../i18n/I18nProvider";
import { formatCustomerCount, formatMoney } from "../shared/format";
import { useLoad } from "../shared/hooks";
import { useWorkspace } from "../shared/workspace/context";
import { Failure, Loading } from "../shared/workspace/parts";
import type { ShopTotals } from "./backoffice";
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
    const columns: Column<ShopTotals>[] = [
      { id: "shop", header: t("totals.shop"), rowHeader: true, cell: (shop) => shop.name },
      { id: "outstanding", header: t("overview.outstanding"), numeric: true, cell: (shop) => formatMoney(shop.outstanding, language) },
      { id: "debtors", header: t("overview.debtors"), numeric: true, cell: (shop) => formatCustomerCount(shop.debtors, language) },
      { id: "overdue", header: t("overview.overdue"), numeric: true, cell: (shop) => formatMoney(shop.overdue, language) },
      { id: "dueToday", header: t("overview.dueToday"), numeric: true, cell: (shop) => formatMoney(shop.dueToday, language) },
    ];
    body = (
      <DataTable
        caption={t("totals.title")}
        columns={columns}
        items={items}
        rowKey={(shop) => shop.shopId}
        foot={[
          t("totals.total"),
          formatMoney(total.outstanding, language),
          formatCustomerCount(total.debtors, language),
          formatMoney(total.overdue, language),
          formatMoney(total.dueToday, language),
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
