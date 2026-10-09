import { Fragment, type ReactNode, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { formatMoney } from "../format";
import { useLoad, usePagedList, useSubmit } from "../hooks";
import type { Column } from "../layout";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { useMay } from "../workspace/context";
import { errorText, Failure, FieldError, formatInstant, Loading, LoadMore } from "../workspace/parts";
import { stockQtyText } from "../workspace/StockNotes";
import { type ScanHost, MAX_BARCODES } from "./barcode";
import { DOCUMENT_KIND_LABELS, Fact, kindText, labelOf, Listing, maySell, moneyOrNone, NONE, qtyWithUnit, useStock, useStockSettings } from "./parts";
import { readCount } from "./quantity";
import { ScanField } from "./ScanField";
import { type ItemStockPatch, type Movement, SALE_KIND, type StockItem, type StockSettings } from "./stockApi";
import { OnHand } from "./StockScreen";

/**
 * How an item is counted: whether it is, in which unit, the level it runs low at, and its barcodes.
 * For those who may change goods. The server refuses what cannot be (a unit changed after movements,
 * counting turned off while something is on hand, a barcode another item has) and says why.
 */
function ItemStockForm({
  item,
  settings,
  host,
  onSaved,
  onClose,
}: {
  item: StockItem;
  settings: StockSettings | null;
  host?: ScanHost | undefined;
  onSaved: () => void;
  onClose: () => void;
}) {
  const { t, language } = useI18n();
  const stock = useStock();
  const [tracked, setTracked] = useState(item.tracked);
  const [unit, setUnit] = useState(item.unit);
  const [low, setLow] = useState(item.lowStock === null ? "" : stockQtyText(item.lowStock));
  const [codes, setCodes] = useState<string[]>(item.barcodes);
  const [problem, setProblem] = useState<string | null>(null);
  const save = useSubmit((patch: ItemStockPatch, key) => stock.updateItem(item.id, patch, key).then(onSaved));
  const pending = save.state.status === "pending";
  const units = settings?.units ?? [];
  // An item the catalog keeps in a unit the stock does not use keeps showing it, so nothing is changed unasked.
  const unitOptions = units.some((known) => known.key === unit) ? units.map((known) => known.key) : [unit, ...units.map((known) => known.key)];

  const send = () => {
    const patch: ItemStockPatch = { tracked, barcodes: codes };
    if (unit !== item.unit) {
      patch.unit = unit;
    }
    if (low.trim() === "") {
      if (item.lowStock !== null) {
        patch.lowStock = null;
      }
    } else {
      const level = readCount(low);
      if (!level.ok) {
        setProblem(t("stock.qty.invalid"));
        return;
      }
      patch.lowStock = level.api;
    }
    save.submit(patch);
  };

  return (
    <div className="form notice" role="group" aria-label={t("stock.item.edit")}>
      {save.state.status === "error" ? (
        <p className="field__error" role="alert">
          {errorText(save.state.error, t)}
        </p>
      ) : null}
      <label className="choice">
        <input type="checkbox" checked={tracked} disabled={pending} onChange={(event) => setTracked(event.target.checked)} />
        <span>{t("stock.item.tracked")}</span>
      </label>
      <div className="field">
        <label htmlFor="stock-item-unit">{t("stock.item.unit")}</label>
        <select id="stock-item-unit" className="input" value={unit} disabled={pending} onChange={(event) => setUnit(event.target.value)}>
          {unitOptions.map((key) => (
            <option key={key} value={key}>
              {labelOf(units, key, language)}
            </option>
          ))}
        </select>
        <p className="field__hint">{t("stock.item.unit.hint")}</p>
      </div>
      <div className="field">
        <label htmlFor="stock-item-low">{t("stock.item.low")}</label>
        <input
          id="stock-item-low"
          className="input"
          inputMode="decimal"
          autoComplete="off"
          value={low}
          disabled={pending}
          aria-invalid={problem !== null}
          aria-describedby="stock-item-low-error stock-item-low-hint"
          onChange={(event) => {
            setLow(event.target.value);
            setProblem(null);
          }}
        />
        <p className="field__hint" id="stock-item-low-hint">
          {t("stock.item.low.hint")}
        </p>
        <FieldError id="stock-item-low-error" message={problem} />
      </div>
      <div className="field">
        <p className="section-label">{t("stock.item.barcodes")}</p>
        {codes.length === 0 ? <p className="state">{t("stock.item.barcodes.none")}</p> : null}
        <ul className="lines" aria-label={t("stock.item.barcodes")}>
          {codes.map((code) => (
            <li key={code} className="line stock-fact">
              <span>{code}</span>
              <button
                type="button"
                className="button button--small"
                disabled={pending}
                aria-label={t("stock.item.barcode.remove", { code })}
                onClick={() => setCodes(codes.filter((kept) => kept !== code))}
              >
                {t("action.delete")}
              </button>
            </li>
          ))}
        </ul>
        {codes.length < MAX_BARCODES ? (
          <ScanField
            id="stock-item-code"
            label={t("stock.item.barcode.add")}
            host={host}
            disabled={pending}
            onCode={(code) => setCodes((current) => (current.includes(code) ? current : [...current, code]))}
          />
        ) : null}
      </div>
      <p className="actions">
        <button type="button" className="button button--primary" onClick={send} disabled={pending}>
          {pending ? t("state.saving") : t("action.save")}
        </button>
        <button type="button" className="button" onClick={onClose} disabled={pending}>
          {t("action.cancel")}
        </button>
      </p>
    </div>
  );
}

/** Everything that changed how much of the item is on hand, newest first, a page at a time. */
function Movements({ item, settings }: { item: StockItem; settings: StockSettings | null }) {
  const { t, language } = useI18n();
  const can = useMay();
  const stock = useStock();
  const sells = maySell(can);
  const { state, reload, loadMore } = usePagedList((cursor, signal) => stock.movements(item.id, cursor, signal), [stock, item.id]);
  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return <Failure error={state.error} onRetry={reload} />;
  }
  if (state.items.length === 0) {
    return <p className="state">{t("stock.moves.none")}</p>;
  }
  const what = (move: Movement) => {
    const parts: ReactNode[] = [kindText(move.kind, "movement", t)];
    if (move.reason !== null) {
      parts.push(labelOf(settings?.writeOffReasons, move.reason, language));
    }
    if (move.document !== null && move.document.kind === SALE_KIND) {
      // A sale for cash is no document of the documents' screens, whose routes do not answer for it: it
      // is named as what it is, and leads to its own page for one who may open that.
      const name = t("stock.sale.ref", { number: move.document.number });
      parts.push(sells ? <Link to={`/stock/sales/${move.document.id}`}>{name}</Link> : name);
    } else if (move.document !== null) {
      const kind = DOCUMENT_KIND_LABELS[move.document.kind as keyof typeof DOCUMENT_KIND_LABELS];
      parts.push(t("stock.doc.ref", { kind: kind ? t(kind) : move.document.kind, number: move.document.number }));
    }
    if (move.reversed) {
      parts.push(t("stock.moves.reversed"));
    }
    return parts.map((part, index) => (
      <Fragment key={index}>
        {index > 0 ? " · " : null}
        {part}
      </Fragment>
    ));
  };
  const columns: Column<Movement>[] = [
    { id: "when", header: t("stock.col.when"), rowHeader: true, cell: (move) => formatInstant(move.createdAt, language) },
    { id: "what", header: t("stock.col.what"), cell: what },
    { id: "qty", header: t("stock.col.qty"), numeric: true, cell: (move) => qtyWithUnit(move.qty, item.unit) },
    { id: "after", header: t("stock.col.after"), numeric: true, cell: (move) => qtyWithUnit(move.onHandAfter, item.unit) },
  ];
  if (state.items.some((move) => move.cost !== undefined)) {
    columns.push({
      id: "cost",
      header: t("stock.col.cost"),
      numeric: true,
      cell: (move) => moneyOrNone(move.cost?.total, language, move.cost?.currency),
    });
  }
  return (
    <>
      <Listing caption={t("stock.moves.title")} columns={columns} items={state.items} rowKey={(move) => move.id} />
      {state.nextCursor !== null ? <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} /> : null}
    </>
  );
}

/** One item of the stock: what is on hand, its barcodes, what it is worth where that may be seen, its movements. */
export function StockItemScreen({ itemId, host }: { itemId: string; host?: ScanHost | undefined }) {
  const { t, language } = useI18n();
  const can = useMay();
  const stock = useStock();
  const { state, reload } = useLoad((signal) => stock.item(itemId, signal), [stock, itemId]);
  const settings = useStockSettings();
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState(false);

  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return state.error.status === 404 ? <NotFoundScreen /> : <Failure error={state.error} onRetry={reload} />;
  }
  const item = state.data;
  const known = settings.state.status === "ready" ? settings.state.data : null;
  const cost = item.cost;
  return (
    <>
      <h2 className="subject">{item.name}</h2>
      {saved && !editing ? (
        <p className="notice notice--done" role="status">
          {t("stock.item.saved")}
        </p>
      ) : null}
      <dl className="facts">
        <Fact name={t("stock.col.onHand")}>
          <OnHand item={item} />
        </Fact>
        <Fact name={t("stock.col.price")}>{formatMoney(item.price, language)}</Fact>
        <Fact name={t("stock.item.unit")}>{labelOf(known?.units, item.unit, language)}</Fact>
        <Fact name={t("stock.item.low")}>{item.lowStock === null ? NONE : qtyWithUnit(item.lowStock, item.unit)}</Fact>
        <Fact name={t("stock.item.barcodes")}>{item.barcodes.length === 0 ? NONE : item.barcodes.join(", ")}</Fact>
        {item.lastSaleAt !== null ? (
          <Fact name={t("stock.item.lastSale")}>{formatInstant(item.lastSaleAt, language)}</Fact>
        ) : null}
        {/* Cost is here only when the server sent it: a member who may not see it gets no row at all. */}
        {cost !== undefined ? (
          <>
            <Fact name={t("stock.col.average")}>{moneyOrNone(cost.average, language, cost.currency)}</Fact>
            <Fact name={t("stock.col.value")}>{moneyOrNone(cost.value, language, cost.currency)}</Fact>
            <Fact name={t("stock.col.margin")}>{moneyOrNone(cost.margin, language, "UZS")}</Fact>
          </>
        ) : null}
      </dl>
      {can("goods.edit") && !editing ? (
        <p className="actions">
          <button
            type="button"
            className="button"
            onClick={() => {
              setSaved(false);
              setEditing(true);
            }}
          >
            {t("stock.item.edit")}
          </button>
        </p>
      ) : null}
      {can("goods.edit") && editing ? (
        <ItemStockForm
          item={item}
          settings={known}
          host={host}
          onSaved={() => {
            setEditing(false);
            setSaved(true);
            reload();
          }}
          onClose={() => setEditing(false)}
        />
      ) : null}
      <h3 className="section-label">{t("stock.moves.title")}</h3>
      <Movements item={item} settings={known} />
      <p className="actions">
        <Link to="/stock" className="button">
          {t("nav.stock")}
        </Link>
      </p>
    </>
  );
}
