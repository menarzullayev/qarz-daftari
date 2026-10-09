import { useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { formatMoney } from "../format";
import { useLoad } from "../hooks";
import type { Column } from "../layout";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { useMay } from "../workspace/context";
import { Failure, formatInstant, Loading } from "../workspace/parts";
import { Listing, moneyOrNone, NONE, qtyWithUnit, useStock } from "./parts";
import type { StockItem, StockReport } from "./stockApi";

/** The periods "not sold" and "sold below cost" may look back over, in days. */
export const REPORT_DAYS = [7, 30, 90, 180, 365] as const;

type BelowCost = StockReport["soldBelowCost"]["sales"][number];

/**
 * What the stock is worth and where money sits or leaks: what does not sell, what was sold at a loss,
 * what runs low. Opened by those who may see cost, and by nobody else: all of it is about cost.
 *
 * Amounts of different currencies stand side by side and are never added: the cost of the stock is one
 * figure for each currency. The margin is of the items whose cost is kept in so'm, the currency they
 * are sold in.
 */
export function StockReportScreen() {
  const { t, language } = useI18n();
  const can = useMay();
  const stock = useStock();
  const [days, setDays] = useState<number>(30);
  const allowed = can("stock.costs.view");
  const { state, reload } = useLoad((signal) => (allowed ? stock.report(days, signal) : Promise.resolve(null)), [stock, days, allowed]);

  if (!allowed) {
    return <NotFoundScreen />;
  }
  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return state.error.status === 404 ? <NotFoundScreen /> : <Failure error={state.error} onRetry={reload} />;
  }
  const report = state.data;
  if (report === null) {
    return <NotFoundScreen />;
  }
  const { totals } = report;

  const itemLink = (item: { id: string; name: string }) => <Link to={`/stock/items/${item.id}`}>{item.name}</Link>;
  const idle: Column<StockItem>[] = [
    { id: "item", header: t("stock.col.item"), rowHeader: true, cell: itemLink },
    { id: "onHand", header: t("stock.col.onHand"), numeric: true, cell: (item) => qtyWithUnit(item.onHand, item.unit) },
    {
      id: "lastSale",
      header: t("stock.item.lastSale"),
      cell: (item) => (item.lastSaleAt === null ? t("stock.report.neverSold") : formatInstant(item.lastSaleAt, language)),
    },
    {
      id: "value",
      header: t("stock.col.value"),
      numeric: true,
      cell: (item) => moneyOrNone(item.cost?.value, language, item.cost?.currency),
    },
  ];
  const below: Column<BelowCost>[] = [
    {
      id: "item",
      header: t("stock.col.item"),
      rowHeader: true,
      cell: (sale) => itemLink({ id: sale.itemId, name: sale.name }),
    },
    { id: "when", header: t("stock.col.when"), cell: (sale) => formatInstant(sale.createdAt, language) },
    { id: "qty", header: t("stock.col.qty"), numeric: true, cell: (sale) => qtyWithUnit(sale.qty, sale.unit) },
    { id: "sale", header: t("stock.report.soldFor"), numeric: true, cell: (sale) => formatMoney(sale.saleTotal, language) },
    { id: "cost", header: t("stock.col.cost"), numeric: true, cell: (sale) => formatMoney(sale.costTotal, language) },
    { id: "loss", header: t("stock.report.loss"), numeric: true, cell: (sale) => formatMoney(sale.loss, language) },
  ];
  const low: Column<StockItem>[] = [
    { id: "item", header: t("stock.col.item"), rowHeader: true, cell: itemLink },
    { id: "onHand", header: t("stock.col.onHand"), numeric: true, cell: (item) => qtyWithUnit(item.onHand, item.unit) },
    {
      id: "level",
      header: t("stock.item.low"),
      numeric: true,
      cell: (item) => (item.lowStock === null ? NONE : qtyWithUnit(item.lowStock, item.unit)),
    },
  ];

  return (
    <>
      <div className="field">
        <label htmlFor="stock-report-days">{t("stock.report.days")}</label>
        <select id="stock-report-days" className="input" value={days} onChange={(event) => setDays(Number(event.target.value))}>
          {REPORT_DAYS.map((option) => (
            <option key={option} value={option}>
              {t("stock.report.days.option", { count: option })}
            </option>
          ))}
        </select>
      </div>

      <dl className="figures">
        <div className="figure">
          <dt>{t("stock.report.items")}</dt>
          <dd>{totals.items}</dd>
        </div>
        <div className="figure">
          <dt>{t("stock.report.lowCount")}</dt>
          <dd>{totals.low}</dd>
        </div>
        {/* A figure for each currency the stock was bought in; there is no sum of the two anywhere. */}
        {totals.cost.map((cost) => (
          <div key={cost.currency} className="figure">
            <dt>{t(cost.currency === "USD" ? "stock.report.cost.usd" : "stock.report.cost.uzs")}</dt>
            <dd className="money">{formatMoney(cost.value, language, cost.currency)}</dd>
          </div>
        ))}
        <div className="figure">
          <dt>{t("stock.report.selling")}</dt>
          <dd className="money">{formatMoney(totals.selling, language)}</dd>
        </div>
        <div className="figure">
          <dt>{t("stock.report.margin")}</dt>
          <dd className="money">{formatMoney(totals.margin.margin, language)}</dd>
          <dd className="figure__note">
            {t("stock.report.margin.note", {
              selling: formatMoney(totals.margin.selling, language),
              cost: formatMoney(totals.margin.cost, language),
            })}
          </dd>
        </div>
      </dl>

      <h3 className="section-label">{t("stock.report.notSold", { count: report.days })}</h3>
      {report.notSold.items.length === 0 ? (
        <p className="state">{t("stock.report.notSold.none")}</p>
      ) : (
        <Listing caption={t("stock.report.notSold", { count: report.days })} columns={idle} items={report.notSold.items} rowKey={(item) => item.id} />
      )}
      {report.notSold.more ? <p className="hint">{t("stock.report.more")}</p> : null}

      <h3 className="section-label">{t("stock.report.below", { count: report.days })}</h3>
      {report.soldBelowCost.sales.length === 0 ? (
        <p className="state">{t("stock.report.below.none")}</p>
      ) : (
        <Listing
          caption={t("stock.report.below", { count: report.days })}
          columns={below}
          items={report.soldBelowCost.sales}
          rowKey={(sale) => `${sale.itemId}/${sale.createdAt}/${sale.qty}`}
        />
      )}
      {report.soldBelowCost.more ? <p className="hint">{t("stock.report.more")}</p> : null}

      <h3 className="section-label">{t("stock.report.low")}</h3>
      {report.lowStock.items.length === 0 ? (
        <p className="state">{t("stock.report.low.none")}</p>
      ) : (
        <Listing caption={t("stock.report.low")} columns={low} items={report.lowStock.items} rowKey={(item) => item.id} />
      )}
      {report.lowStock.more ? <p className="hint">{t("stock.report.more")}</p> : null}

      <p className="actions">
        <Link to="/stock" className="button">
          {t("nav.stock")}
        </Link>
      </p>
    </>
  );
}
