import { useEffect, useRef, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { ApiError } from "../api";
import { formatMoney } from "../format";
import { formatQty, lineTotal, MAX_QTY_THOUSANDTHS, parsePrice, QTY_SCALE } from "../goods";
import { useSubmit } from "../hooks";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { useMay } from "../workspace/context";
import { errorText, Failure, FieldError, Loading } from "../workspace/parts";
import { StockRefusal } from "../workspace/StockNotes";
import type { ScanHost } from "./barcode";
import { ItemFinder } from "./ItemFinder";
import { maySell, qtyWithUnit, useStock, useStockSettings } from "./parts";
import { MAX_DOCUMENT_TOTAL, MAX_NOTE, readQty, tidy } from "./quantity";
import { SALE_METHOD_LABELS, SaleDetails, SaleWarnings } from "./saleParts";
import { type ItemFilter, type NewSale, PAYMENT_METHODS, type PaymentMethod, type RecordedSale, type StockItem, type StockSettings } from "./stockApi";

/** The most lines one sale holds (backend/src/qarz/domain/stock.py, MAX_SALE_LINES). */
export const MAX_SALE_LINES = 100;

/** What the finder offers by name: every item of the catalog. One that is not counted is sold too, with no movement. */
const SOLD_ITEMS: ItemFilter = "all";

/** A line while it is being typed: the quantity and the price are still text. */
export type SaleDraftLine = { item: StockItem; qtyText: string; priceText: string };

/** What the stepper adds or takes: one piece, or a tenth of a weighed unit. Whole thousandths. */
export function stepOf(unit: string, settings: StockSettings): number {
  return settings.units.find((known) => known.key === unit)?.weighed ? QTY_SCALE / 10 : QTY_SCALE;
}

/**
 * The text of a quantity moved by `by` thousandths. Unchanged where the result would be nothing or too
 * much; a text that cannot be read becomes one step when it is stepped up.
 */
export function stepQty(qtyText: string, by: number): string {
  const read = readQty(qtyText);
  const next = (read.ok ? read.thousandths : 0) + by;
  if ((!read.ok && by < 0) || next <= 0 || next > MAX_QTY_THOUSANDTHS) {
    return qtyText;
  }
  return formatQty(next);
}

/** Whether the quantity of a unit is typed with a comma: a weighed one, and any unit the stock does not name. */
function takesFractions(unit: string, settings: StockSettings): boolean {
  return settings.units.find((known) => known.key === unit)?.weighed !== false;
}

/**
 * Adds a found item to the sale. An item that is already there gets one step more instead of a second
 * line: the server takes an item once in a sale, and three scans of one good are three of it.
 */
export function addToSale(lines: readonly SaleDraftLine[], item: StockItem, settings: StockSettings): SaleDraftLine[] {
  const step = stepOf(item.unit, settings);
  if (lines.some((line) => line.item.id === item.id)) {
    return lines.map((line) => (line.item.id === item.id ? { ...line, qtyText: stepQty(line.qtyText, step) } : line));
  }
  return [...lines, { item, qtyText: formatQty(QTY_SCALE), priceText: String(item.price) }];
}

type LineReading =
  | { ok: true; qty: string; price: number; total: number }
  | { ok: false; qty: boolean; price: boolean; tooSmall: boolean };

function readLine(line: SaleDraftLine): LineReading {
  const qty = readQty(line.qtyText);
  const price = parsePrice(line.priceText);
  if (!qty.ok || !price.ok) {
    return { ok: false, qty: !qty.ok, price: !price.ok, tooSmall: false };
  }
  // Rounded as the server rounds it; null when the line comes to less than one so'm.
  const total = lineTotal(qty.thousandths, price.amount);
  return total === null ? { ok: false, qty: false, price: false, tooSmall: true } : { ok: true, qty: qty.api, price: price.amount, total };
}

/**
 * Why the server refused a sale: its own sentence, then which item and how much of it there is when the
 * shop refuses sales beyond stock, and the lines a check failed on.
 */
function SaleRefusal({ error }: { error: ApiError }) {
  const { t } = useI18n();
  const lines = new Set<number>();
  for (const name of Object.keys(error.fields)) {
    const line = /^lines\.(\d+)\./.exec(name);
    if (line) {
      lines.add(Number(line[1]) + 1);
    }
  }
  return (
    <div className="notice notice--error" role="alert">
      <p>{errorText(error, t)}</p>
      <StockRefusal error={error} />
      {[...lines]
        .sort((a, b) => a - b)
        .map((line) => (
          <p key={line}>{t("stock.doc.refused.line", { line })}</p>
        ))}
    </div>
  );
}

/**
 * A sale for cash at the counter: goods found by a scan, a typed barcode or a part of the name; of each
 * a quantity and this sale's price; how it was paid; "Sotish". No customer, and no debt.
 *
 * It is offered to a member who holds "stock.sell" and "stock.view" both, like the quick receipt: the
 * section it sits in is the stock's, which "stock.view" opens, and a seller holds both by default. For
 * anyone else it is not a screen, and nothing is asked on their behalf.
 */
export function SaleScreen({ host }: { host?: ScanHost | undefined }) {
  const can = useMay();
  return maySell(can) ? <Counter host={host} /> : <NotFoundScreen />;
}

function Counter({ host }: { host?: ScanHost | undefined }) {
  const settings = useStockSettings();
  if (settings.state.status === "loading") {
    return <Loading />;
  }
  if (settings.state.status === "error") {
    return <Failure error={settings.state.error} onRetry={settings.reload} />;
  }
  return <SaleForm settings={settings.state.data} host={host} />;
}

function SaleForm({ settings, host }: { settings: StockSettings; host?: ScanHost | undefined }) {
  const { t, language } = useI18n();
  const stock = useStock();
  const [lines, setLines] = useState<SaleDraftLine[]>([]);
  const [method, setMethod] = useState<PaymentMethod>("cash");
  const [note, setNote] = useState("");
  // After a refused "Sotish" every unreadable field is marked, the ones still empty too.
  const [checked, setChecked] = useState(false);
  const [saved, setSaved] = useState<RecordedSale | null>(null);
  const again = useRef<HTMLButtonElement>(null);
  const sell = useSubmit((sale: NewSale, key) =>
    stock.recordSale(sale, key).then((recorded) => {
      setSaved(recorded);
      setLines([]);
      setNote("");
      setMethod("cash");
      setChecked(false);
      return recorded;
    }),
  );
  const pending = sell.state.status === "pending";

  // The sale is saved: the focus goes to "Yangi savdo", so that Enter starts the next one.
  const savedId = saved?.id ?? null;
  useEffect(() => {
    if (savedId !== null) {
      again.current?.focus();
    }
  }, [savedId]);

  const readings = lines.map(readLine);
  const total = readings.reduce((sum, reading) => (reading.ok ? sum + reading.total : sum), 0);
  const noteText = tidy(note);
  const noteTooLong = noteText !== null && [...noteText].length > MAX_NOTE;
  const full = lines.length >= MAX_SALE_LINES;

  const startOver = () => {
    setSaved(null);
    sell.reset();
  };
  const pick = (item: StockItem) => {
    // A good found while the last sale is still shown begins the next one.
    setSaved(null);
    sell.reset();
    setLines((current) => (current.length >= MAX_SALE_LINES && !current.some((line) => line.item.id === item.id) ? current : addToSale(current, item, settings)));
  };
  const change = (itemId: string, patch: Partial<SaleDraftLine>) => {
    sell.reset();
    setLines((current) => current.map((line) => (line.item.id === itemId ? { ...line, ...patch } : line)));
  };
  const send = () => {
    setChecked(true);
    const ready: NewSale["lines"][number][] = [];
    for (const [index, reading] of readings.entries()) {
      const line = lines[index];
      if (!reading.ok || line === undefined) {
        return;
      }
      ready.push({ itemId: line.item.id, qty: reading.qty, price: reading.price });
    }
    if (ready.length === 0 || noteTooLong || total > MAX_DOCUMENT_TOTAL) {
      return;
    }
    sell.submit({ lines: ready, method, note: noteText });
  };

  return (
    <>
      <ItemFinder id="stock-sale-find" filter={SOLD_ITEMS} disabled={pending} host={host} onPick={pick} />
      {saved !== null ? (
        <>
          <div className="notice notice--done" role="status">
            <p>{t("stock.sale.done", { number: saved.number })}</p>
            <p className="balance sale-total">
              <span>{t("stock.doc.total")}</span> <strong>{formatMoney(saved.total, language)}</strong>
            </p>
            <SaleWarnings warnings={saved.warnings} />
          </div>
          <p className="actions">
            <button ref={again} type="button" className="button button--primary" onClick={startOver}>
              {t("stock.sale.new")}
            </button>
            <Link to="/stock/sales" className="button">
              {t("nav.cashSales")}
            </Link>
          </p>
          <SaleDetails sale={saved} />
        </>
      ) : (
        <>
          {sell.state.status === "error" ? <SaleRefusal error={sell.state.error} /> : null}
          {lines.length === 0 ? (
            <p className="state">{t("stock.sale.lines.none")}</p>
          ) : (
            <ul className="lines" aria-label={t("stock.sale.lines")}>
              {lines.map((line, index) => {
                const { item } = line;
                const reading = readings[index];
                const bad = reading !== undefined && !reading.ok ? reading : null;
                // An emptied field is not an error while the seller is still on the way back to it.
                const qtyError = bad?.qty && (checked || line.qtyText.trim() !== "") ? t("stock.qty.positive") : null;
                const priceError = bad?.price && (checked || line.priceText.trim() !== "") ? t("stock.sale.price.invalid") : null;
                const tooSmall = bad?.tooSmall ? t("stock.sale.line.tooSmall") : null;
                const step = stepOf(item.unit, settings);
                const key = `stock-sale-${item.id}`;
                return (
                  <li key={item.id} className="line">
                    <p className="line__head">
                      <span className="row__name">{item.name}</span>
                      <button
                        type="button"
                        className="button button--small"
                        aria-label={t("stock.line.remove", { name: item.name })}
                        disabled={pending}
                        onClick={() => {
                          sell.reset();
                          setLines((current) => current.filter((other) => other.item.id !== item.id));
                        }}
                      >
                        ×
                      </button>
                    </p>
                    <p className="row__meta">
                      {item.tracked ? t("stock.sale.onHand", { onHand: qtyWithUnit(item.onHand, item.unit) }) : t("stock.untracked")}
                    </p>
                    <div className="sale-line">
                      <div className="sale-qty">
                        <button
                          type="button"
                          className="button button--icon"
                          aria-label={t("stock.sale.qty.less", { name: item.name })}
                          disabled={pending || stepQty(line.qtyText, -step) === line.qtyText}
                          onClick={() => change(item.id, { qtyText: stepQty(line.qtyText, -step) })}
                        >
                          −
                        </button>
                        <input
                          className="input sale-qty__input"
                          // A weighed good is sold by a part of its unit: the keyboard has the comma.
                          inputMode={takesFractions(item.unit, settings) ? "decimal" : "numeric"}
                          autoComplete="off"
                          value={line.qtyText}
                          aria-label={t("stock.sale.qty", { name: item.name, unit: item.unit })}
                          aria-invalid={qtyError !== null}
                          aria-describedby={`${key}-qty-error`}
                          disabled={pending}
                          onChange={(event) => change(item.id, { qtyText: event.target.value })}
                        />
                        <button
                          type="button"
                          className="button button--icon"
                          aria-label={t("stock.sale.qty.more", { name: item.name })}
                          disabled={pending || stepQty(line.qtyText, step) === line.qtyText}
                          onClick={() => change(item.id, { qtyText: stepQty(line.qtyText, step) })}
                        >
                          +
                        </button>
                      </div>
                      <input
                        className="input"
                        inputMode="numeric"
                        autoComplete="off"
                        value={line.priceText}
                        aria-label={t("stock.sale.price", { name: item.name })}
                        aria-invalid={priceError !== null}
                        aria-describedby={`${key}-price-error`}
                        disabled={pending}
                        onChange={(event) => change(item.id, { priceText: event.target.value })}
                      />
                    </div>
                    <p className="line__total">{reading?.ok ? formatMoney(reading.total, language) : "—"}</p>
                    <FieldError id={`${key}-qty-error`} message={qtyError} />
                    <FieldError id={`${key}-price-error`} message={priceError} />
                    <FieldError id={`${key}-total-error`} message={tooSmall} />
                  </li>
                );
              })}
            </ul>
          )}
          {full ? <p className="hint">{t("stock.sale.problem.tooMany", { max: MAX_SALE_LINES })}</p> : null}
          {lines.length > 0 ? (
            <>
              <p className="balance sale-total">
                <span>{t("stock.doc.total")}</span> <strong>{formatMoney(total, language)}</strong>
              </p>
              {total > MAX_DOCUMENT_TOTAL ? (
                <p className="field__error" role="alert">
                  {t("stock.sale.problem.total")}
                </p>
              ) : null}
              <div className="field">
                <div className="toggle" role="group" aria-label={t("stock.method")}>
                  {PAYMENT_METHODS.map((option) => (
                    <button
                      key={option}
                      type="button"
                      className="toggle__option"
                      aria-pressed={method === option}
                      disabled={pending}
                      onClick={() => {
                        sell.reset();
                        setMethod(option);
                      }}
                    >
                      {t(SALE_METHOD_LABELS[option])}
                    </button>
                  ))}
                </div>
                {/* Said only in a shop that keeps a cash book: there the money is entered as income too. */}
                {settings.cashBook ? <p className="field__hint">{t("stock.sale.cashBook")}</p> : null}
              </div>
              <div className="field">
                <label htmlFor="stock-sale-note">{t("stock.doc.note")}</label>
                <input
                  id="stock-sale-note"
                  className="input"
                  autoComplete="off"
                  value={note}
                  maxLength={MAX_NOTE * 2}
                  disabled={pending}
                  aria-invalid={noteTooLong}
                  aria-describedby="stock-sale-note-error"
                  onChange={(event) => {
                    sell.reset();
                    setNote(event.target.value);
                  }}
                />
                <FieldError id="stock-sale-note-error" message={noteTooLong ? t("stock.doc.problem.note", { max: MAX_NOTE }) : null} />
              </div>
            </>
          ) : null}
          <p className="actions">
            {lines.length > 0 ? (
              <button type="button" className="button button--primary" onClick={send} disabled={pending}>
                {pending ? t("state.saving") : t("stock.sale.submit")}
              </button>
            ) : null}
            <Link to="/stock/sales" className="button">
              {t("nav.cashSales")}
            </Link>
            <Link to="/stock" className="button">
              {t("nav.stock")}
            </Link>
          </p>
        </>
      )}
    </>
  );
}
