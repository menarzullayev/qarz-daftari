import { useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { formatMoney, tashkentDay } from "../format";
import { useLoad, usePagedList, useSubmit } from "../hooks";
import { type Column, useDesktop } from "../layout";
import type { Role } from "../navigation";
import { addDays, compareDays, parseIsoDate, toIsoDate } from "../promise";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { useMay, useWorkspace } from "../workspace/context";
import { Empty, Failure, FieldError, formatInstant, Loading, LoadMore } from "../workspace/parts";
import { OptionPicker, type PickLoader, type PickOption } from "./OptionPicker";
import { CancelForm, Listing, maySell, useStock } from "./parts";
import { goodsText, SALE_METHOD_LABELS, SALE_STATUS_LABELS, SaleDetails, SaleStatusBadge, sellerRoleText, sellerText } from "./saleParts";
import { type Sale, SALE_STATUSES, type SaleStatus, type SalesPage, type SaleSummary } from "./stockApi";

/** The longest stretch of days the server lists at once. */
export const MAX_SALES_DAYS = 366;

/** Whose sales are shown: everyone's, the reader's own, or one member's by their membership. */
type Seller = { kind: typeof EVERYONE } | { kind: typeof MINE } | { kind: "member"; id: string };
const EVERYONE = "all";
const MINE = "mine";

type Period = { from: string; to: string };

/** The two days as typed, when they are a stretch the server takes: in order, and no longer than it lists. */
export function readPeriod(from: string, to: string): Period | null {
  const first = parseIsoDate(from);
  const last = parseIsoDate(to);
  if (first === null || last === null || compareDays(last, first) < 0) {
    return null;
  }
  return compareDays(last, addDays(first, MAX_SALES_DAYS - 1)) > 0 ? null : { from, to };
}

/** What the first page of a list says about all of it. */
type Summary = Pick<SalesPage, "totals" | "mayCancel">;

/**
 * The cash sales, newest first, with what those that stand came to and how they were paid.
 *
 * At the counter (the Mini App) it is today's sales, everyone's or the reader's own. In the web panel
 * it has filters: the days, an item, who sold, and the state. One of them opened (`saleId`) is drawn in
 * place of the list, which stays loaded behind it: whether a sale may be taken back is what the list's
 * answer says (`may_cancel`), and nothing else offers that.
 *
 * Like the sale's form, it is for a member who holds "stock.sell" and "stock.view" both.
 */
export function SalesScreen({ office = false, saleId = null }: { office?: boolean; saleId?: string | null }) {
  const can = useMay();
  return maySell(can) ? <Sales office={office} saleId={saleId} /> : <NotFoundScreen />;
}

function Sales({ office, saleId }: { office: boolean; saleId: string | null }) {
  const { t, language } = useI18n();
  const stock = useStock();
  const { now } = useWorkspace();
  // A real table on a wide screen of the web panel; rows everywhere else.
  const wide = useDesktop() !== null;
  const today = toIsoDate(tashkentDay(now()));
  // The days as they stand in the two fields, and the last stretch of them that could be asked for.
  const [from, setFrom] = useState(today);
  const [to, setTo] = useState(today);
  const [period, setPeriod] = useState<Period>({ from: today, to: today });
  const [item, setItem] = useState<PickOption | null>(null);
  const [seller, setSeller] = useState<Seller>({ kind: "all" });
  const [status, setStatus] = useState<SaleStatus | "">("");
  const [summary, setSummary] = useState<Summary | null>(null);
  // Who sold, as the rows read so far name them: the members the list can be narrowed to.
  const [sellers, setSellers] = useState<ReadonlyMap<string, Role | null>>(new Map());
  const itemId = item?.id ?? "";
  const sellerKey = seller.kind === "member" ? seller.id : seller.kind;

  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) =>
      stock
        .sales(
          {
            // The counter asks for no day: the server's own today, in Tashkent.
            dayFrom: office ? period.from : undefined,
            dayTo: office ? period.to : undefined,
            itemId,
            mine: seller.kind === "mine",
            sellerId: seller.kind === "member" ? seller.id : undefined,
            status,
            cursor,
          },
          signal,
        )
        .then((page) => {
          if (!signal.aborted) {
            if (cursor === null) {
              setSummary({ totals: page.totals, mayCancel: page.mayCancel });
            }
            setSellers((known) => {
              const next = new Map(known);
              for (const sale of page.items) {
                if (!sale.mine) {
                  next.set(sale.createdBy, sale.sellerRole);
                }
              }
              return next.size === known.size ? known : next;
            });
          }
          return page;
        }),
    [stock, office, period.from, period.to, itemId, sellerKey, status],
  );

  const findItems: PickLoader = (query, cursor, signal) =>
    stock.items({ q: query, filter: "all", cursor, limit: 20 }, signal).then((page) => ({
      options: page.items.map((found) => ({ id: found.id, name: found.name, detail: found.unit })),
      nextCursor: page.nextCursor,
    }));

  const changeDays = (first: string, last: string) => {
    setFrom(first);
    setTo(last);
    const read = readPeriod(first, last);
    if (read !== null) {
      setPeriod(read);
    }
  };

  if (saleId !== null) {
    return <OneSale key={saleId} saleId={saleId} mayCancel={summary?.mayCancel ?? false} onChanged={reload} />;
  }

  const columns: Column<SaleSummary>[] = [
    {
      id: "sale",
      header: t("nav.cashSale"),
      rowHeader: true,
      cell: (sale) => (
        <>
          <Link to={`/stock/sales/${sale.id}`} className="sale-ref">
            {t("stock.sale.ref", { number: sale.number })}
          </Link>
          {sale.status === "cancelled" ? (
            <>
              {" "}
              <SaleStatusBadge status={sale.status} />
            </>
          ) : null}
          {/* In rows, what was sold stands under the sale's name: beside a column's name it had no room. */}
          {wide ? null : <span className="row__meta sale-goods">{goodsText(sale.lines)}</span>}
        </>
      ),
    },
    { id: "when", header: t("stock.col.when"), cell: (sale) => formatInstant(sale.createdAt, language) },
    ...(wide ? [{ id: "goods", header: t("stock.doc.lines"), cell: (sale: SaleSummary) => goodsText(sale.lines) }] : []),
    { id: "total", header: t("stock.doc.total"), numeric: true, cell: (sale) => formatMoney(sale.total, language) },
    { id: "method", header: t("stock.method"), cell: (sale) => t(SALE_METHOD_LABELS[sale.method]) },
    { id: "seller", header: t("stock.sale.seller"), cell: (sale) => sellerText(sale, t) },
  ];

  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else {
    body = (
      <>
        {summary !== null ? (
          <dl className="figures" aria-label={t("stock.sales.totals")}>
            <div className="figure">
              <dt>{t("stock.sales.count")}</dt>
              <dd>{summary.totals.count}</dd>
            </div>
            <div className="figure">
              <dt>{t("stock.sales.total")}</dt>
              <dd className="money">{formatMoney(summary.totals.total, language)}</dd>
            </div>
            {summary.totals.byMethod.map((paid) => (
              <div key={paid.method} className="figure">
                <dt>{t(SALE_METHOD_LABELS[paid.method])}</dt>
                <dd className="money">{formatMoney(paid.total, language)}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        {state.items.length === 0 ? (
          <Empty>{t(office ? "stock.sales.none.match" : "stock.sales.none")}</Empty>
        ) : (
          <>
            <Listing caption={t("nav.cashSales")} columns={columns} items={state.items} rowKey={(sale) => sale.id} />
            {state.nextCursor !== null ? <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} /> : null}
          </>
        )}
      </>
    );
  }

  return (
    <>
      <div className="bar">
        {office ? null : (
          <div className="toggle" role="group" aria-label={t("stock.sales.whose")}>
            <button type="button" className="toggle__option" aria-pressed={seller.kind !== "mine"} onClick={() => setSeller({ kind: "all" })}>
              {t("stock.filter.all")}
            </button>
            <button type="button" className="toggle__option" aria-pressed={seller.kind === "mine"} onClick={() => setSeller({ kind: "mine" })}>
              {t("stock.sales.mine")}
            </button>
          </div>
        )}
        <Link to="/stock/sale" className="button button--primary">
          {t("stock.sale.new")}
        </Link>
      </div>
      {office ? (
        <section className="stock-filters" aria-label={t("stock.sales.filters")}>
          <div className="field">
            <label htmlFor="stock-sales-from">{t("stock.sales.from")}</label>
            <input
              id="stock-sales-from"
              type="date"
              className="input"
              value={from}
              max={today}
              aria-invalid={readPeriod(from, to) === null}
              aria-describedby="stock-sales-days-error"
              onChange={(event) => changeDays(event.target.value, to)}
            />
          </div>
          <div className="field">
            <label htmlFor="stock-sales-to">{t("stock.sales.to")}</label>
            <input
              id="stock-sales-to"
              type="date"
              className="input"
              value={to}
              max={today}
              aria-invalid={readPeriod(from, to) === null}
              aria-describedby="stock-sales-days-error"
              onChange={(event) => changeDays(from, event.target.value)}
            />
          </div>
          {/* Out of all the goods of the catalog, by a part of the name: not the first page of them. */}
          <OptionPicker id="stock-sales-item" label={t("stock.col.item")} value={item} load={findItems} noneLabel={t("stock.sales.item.all")} onChange={setItem} />
          <div className="field">
            <label htmlFor="stock-sales-seller">{t("stock.sale.seller")}</label>
            <select
              id="stock-sales-seller"
              className="input"
              value={sellerKey}
              onChange={(event) => {
                const chosen = event.target.value;
                setSeller(chosen === EVERYONE || chosen === MINE ? { kind: chosen } : { kind: "member", id: chosen });
              }}
            >
              <option value={EVERYONE}>{t("stock.sales.seller.all")}</option>
              <option value={MINE}>{t("stock.sale.seller.you")}</option>
              {/* A member is named by their role and the end of their membership, as the documents' list does. */}
              {[...sellers].map(([id, held]) => (
                <option key={id} value={id}>
                  {`${sellerRoleText(held, t)} · ${id.slice(-6)}`}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="stock-sales-status">{t("stock.col.status")}</label>
            <select
              id="stock-sales-status"
              className="input"
              value={status}
              onChange={(event) => setStatus(SALE_STATUSES.find((known) => known === event.target.value) ?? "")}
            >
              <option value="">{t("stock.docs.status.all")}</option>
              {SALE_STATUSES.map((known) => (
                <option key={known} value={known}>
                  {t(SALE_STATUS_LABELS[known])}
                </option>
              ))}
            </select>
          </div>
        </section>
      ) : (
        <h2 className="section-label">{t("stock.sales.today")}</h2>
      )}
      {office ? <FieldError id="stock-sales-days-error" message={readPeriod(from, to) === null ? t("stock.sales.period.invalid", { max: MAX_SALES_DAYS }) : null} /> : null}
      {body}
      <p className="actions">
        <Link to="/stock" className="button">
          {t("nav.stock")}
        </Link>
      </p>
    </>
  );
}

/**
 * One sale by its address. It is taken back with a reason, which stays in the books: offered while the
 * sale stands, and only when the list's answer said that this member may (`mayCancel`).
 */
function OneSale({ saleId, mayCancel, onChanged }: { saleId: string; mayCancel: boolean; onChanged: () => void }) {
  const { t } = useI18n();
  const stock = useStock();
  const { state, reload } = useLoad((signal) => stock.sale(saleId, signal), [stock, saleId]);
  // What the cancellation answered with; shown in place of what was read before it.
  const [changed, setChanged] = useState<Sale | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const cancel = useSubmit((reason: string, key) =>
    stock.cancelSale(saleId, reason, key).then((cancelled) => {
      setChanged(cancelled);
      setCancelling(false);
      onChanged();
      return cancelled;
    }),
  );

  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return state.error.status === 404 ? <NotFoundScreen /> : <Failure error={state.error} onRetry={reload} />;
  }
  const sale = changed ?? state.data;
  return (
    <>
      {changed !== null ? (
        <p className="notice notice--done" role="status">
          {t("stock.sale.cancelled")}
        </p>
      ) : null}
      <SaleDetails sale={sale} />
      {mayCancel && sale.status === "posted" && !cancelling ? (
        <p className="actions">
          <button
            type="button"
            className="button button--danger"
            onClick={() => {
              cancel.reset();
              setCancelling(true);
            }}
          >
            {t("stock.sale.cancel")}
          </button>
        </p>
      ) : null}
      {mayCancel && sale.status === "posted" && cancelling ? (
        <>
          <p className="hint">{t("stock.sale.cancel.hint")}</p>
          <CancelForm
            id="stock-sale-cancel"
            label={t("stock.doc.cancel.reason")}
            submitLabel={t("stock.sale.cancel")}
            pending={cancel.state.status === "pending"}
            error={cancel.state.status === "error" ? cancel.state.error : null}
            onSubmit={(reason) => cancel.submit(reason)}
            onClose={() => {
              setCancelling(false);
              cancel.reset();
            }}
          />
        </>
      ) : null}
      <p className="actions">
        <Link to="/stock/sales" className="button">
          {t("nav.cashSales")}
        </Link>
      </p>
    </>
  );
}
