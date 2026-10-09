import { useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { MessageKey } from "../../i18n/types";
import { formatMoney } from "../format";
import { usePagedList, useSubmit } from "../hooks";
import { BoxIcon } from "../icons";
import type { Column } from "../layout";
import { Link } from "../router";
import { useMay } from "../workspace/context";
import { Badge, Empty, errorText, Failure, Loading, LoadMore } from "../workspace/parts";
import type { ScanHost } from "./barcode";
import { LookupNotice, useLookup } from "./ItemFinder";
import { Listing, moneyOrNone, qtyWithUnit, useStock, useStockSettings } from "./parts";
import { isNegative } from "./quantity";
import { ScanField } from "./ScanField";
import { ITEM_FILTERS, type ItemFilter, type StockItem } from "./stockApi";

const FILTER_LABELS: Readonly<Record<ItemFilter, MessageKey>> = {
  tracked: "stock.filter.tracked",
  low: "stock.filter.low",
  all: "stock.filter.all",
};

/** What is on hand of an item, with the two things that need a second look said in words. */
export function OnHand({ item }: { item: StockItem }) {
  const { t } = useI18n();
  if (!item.tracked) {
    return <span className="row__meta">{t("stock.untracked")}</span>;
  }
  return (
    <>
      <span>{qtyWithUnit(item.onHand, item.unit)}</span>{" "}
      {isNegative(item.onHand) ? <Badge tone="danger">{t("stock.negative")}</Badge> : null}
      {item.low && !isNegative(item.onHand) ? <Badge tone="warning">{t("stock.low")}</Badge> : null}
    </>
  );
}

/**
 * "Refuse sales beyond stock", for those who may change the shop's rules. Off, a sale may take an item
 * below zero and the seller is warned; on, such a sale is refused.
 */
function RefuseNegative() {
  const { t } = useI18n();
  const stock = useStock();
  const { state, reload } = useStockSettings();
  const save = useSubmit((refuse: boolean, key) => stock.updateSettings(refuse, key).then(reload));
  if (state.status !== "ready") {
    return null;
  }
  const pending = save.state.status === "pending";
  return (
    <section aria-labelledby="stock-settings-title">
      <h2 id="stock-settings-title" className="section-label">
        {t("stock.settings.title")}
      </h2>
      <label className="choice">
        <input
          type="checkbox"
          checked={state.data.refuseNegative}
          disabled={pending}
          onChange={(event) => save.submit(event.target.checked)}
        />
        <span>{t("stock.settings.refuse")}</span>
      </label>
      <p className="hint">{t("stock.settings.refuse.hint")}</p>
      {save.state.status === "error" ? (
        <p className="field__error" role="alert">
          {errorText(save.state.error, t)}
        </p>
      ) : null}
    </section>
  );
}

/**
 * The stock: what is on hand of each item. Found by a scan, by a typed barcode or by a part of the
 * name; filtered to what runs low. What goods cost is in the list only when the server sent it, which
 * it does for a member who may see cost.
 */
export function StockScreen({ host, office = false }: { host?: ScanHost | undefined; office?: boolean }) {
  const { t, language } = useI18n();
  const can = useMay();
  const stock = useStock();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ItemFilter>("tracked");
  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) => stock.items({ q: query, filter, cursor }, signal),
    [stock, query, filter],
  );
  const found = useLookup();
  // A code no item has, which the member chose to give to one of the items in the list.
  const [attaching, setAttaching] = useState<string | null>(null);
  const attach = useSubmit((target: { item: StockItem; code: string }, key) =>
    stock.updateItem(target.item.id, { barcodes: [...target.item.barcodes, target.code] }, key).then((item) => {
      setAttaching(null);
      found.reset();
      reload();
      return item;
    }),
  );

  const columns: Column<StockItem>[] = [
    {
      id: "item",
      header: t("stock.col.item"),
      rowHeader: true,
      cell: (item) => <Link to={`/stock/items/${item.id}`}>{item.name}</Link>,
    },
    { id: "onHand", header: t("stock.col.onHand"), numeric: true, cell: (item) => <OnHand item={item} /> },
    { id: "price", header: t("stock.col.price"), numeric: true, cell: (item) => formatMoney(item.price, language) },
  ];
  // The cost columns exist only when the answer carries cost: for a seller there is nothing to hide.
  const items = state.status === "ready" ? state.items : [];
  if (items.some((item) => item.cost !== undefined)) {
    columns.push(
      {
        id: "average",
        header: t("stock.col.average"),
        numeric: true,
        cell: (item) => moneyOrNone(item.cost?.average, language, item.cost?.currency),
      },
      {
        id: "value",
        header: t("stock.col.value"),
        numeric: true,
        cell: (item) => (item.tracked ? moneyOrNone(item.cost?.value, language, item.cost?.currency) : null),
      },
      {
        id: "margin",
        header: t("stock.col.margin"),
        numeric: true,
        cell: (item) => moneyOrNone(item.cost?.margin, language, "UZS"),
      },
    );
  }
  if (attaching !== null) {
    columns.push({
      id: "attach",
      header: t("stock.col.actions"),
      cell: (item) => (
        <button
          type="button"
          className="button button--small"
          disabled={attach.state.status === "pending"}
          onClick={() => attach.submit({ item, code: attaching })}
        >
          {t("stock.attach.here")}
        </button>
      ),
    });
  }

  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.items.length === 0) {
    body = (
      <Empty icon={<BoxIcon />}>
        {query !== "" ? t("stock.none.match") : filter === "low" ? t("stock.none.low") : t("stock.none")}
      </Empty>
    );
  } else {
    body = (
      <>
        <Listing caption={t("stock.table")} columns={columns} items={state.items} rowKey={(item) => item.id} />
        {state.nextCursor !== null ? (
          <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} />
        ) : null}
      </>
    );
  }

  const hit = found.state.status === "found" ? found.state.item : null;
  return (
    <>
      <ScanField
        id="stock-search"
        label={t("stock.search")}
        host={host}
        onCode={(code) => {
          setAttaching(null);
          found.lookup(code);
        }}
        onWords={(words) => {
          found.reset();
          setAttaching(null);
          setQuery(words);
        }}
      />
      <LookupNotice
        state={found.state}
        missing={(code) =>
          // Giving a code to an item changes the catalog: offered to those who may, and to nobody else.
          can("goods.edit") && attaching === null ? (
            <p className="actions">
              <button type="button" className="button" onClick={() => setAttaching(code)}>
                {t("stock.attach.offer")}
              </button>
              {can("stock.receive") ? (
                <Link to="/stock/receipt" className="button">
                  {t("stock.attach.receipt")}
                </Link>
              ) : null}
            </p>
          ) : null
        }
      />
      {attaching !== null ? (
        <div className="notice" role="status">
          <p>{t("stock.attach.choose", { code: attaching })}</p>
          {attach.state.status === "error" ? (
            <p className="field__error" role="alert">
              {errorText(attach.state.error, t)}
            </p>
          ) : null}
          <p className="actions">
            <button type="button" className="button" onClick={() => setAttaching(null)}>
              {t("action.cancel")}
            </button>
          </p>
        </div>
      ) : null}
      {hit ? (
        <div className="notice notice--done" role="status">
          <p className="row__name">
            <Link to={`/stock/items/${hit.id}`}>{hit.name}</Link>
          </p>
          <p>
            <OnHand item={hit} />
          </p>
          <p className="row__meta">{formatMoney(hit.price, language)}</p>
        </div>
      ) : null}
      <div className="bar">
        <div className="toggle" role="group" aria-label={t("stock.filter")}>
          {ITEM_FILTERS.map((option) => (
            <button
              key={option}
              type="button"
              className="toggle__option"
              aria-pressed={filter === option}
              onClick={() => setFilter(option)}
            >
              {t(FILTER_LABELS[option])}
            </button>
          ))}
        </div>
        {can("stock.receive") ? (
          <Link to="/stock/receipt" className="button button--primary">
            {t("stock.receipt.quick")}
          </Link>
        ) : null}
        {/* The web panel has the documents as a section of its own; at the counter they are reached from here. */}
        {!office && (can("stock.receive") || can("stock.adjust")) ? (
          <Link to="/stock/documents" className="button">
            {t("stock.docs.open")}
          </Link>
        ) : null}
        {can("stock.costs.view") ? (
          <Link to="/stock/report" className="button">
            {t("stock.report.open")}
          </Link>
        ) : null}
      </div>
      {body}
      {can("settings.edit") ? <RefuseNegative /> : null}
    </>
  );
}
