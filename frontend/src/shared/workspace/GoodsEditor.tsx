import { useEffect, useState, type KeyboardEvent } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { CatalogItem, GoodsLine, NewLine } from "../api";
import { formatMoney } from "../format";
import {
  formatQty,
  lineTotal,
  MAX_LINES,
  parsePrice,
  parseQty,
  QTY_SCALE,
  type QtyProblem,
} from "../goods";
import { usePagedList } from "../hooks";
import type { AmountProblem } from "../money";
import { cleanItemName, itemNameProblem, MAX_ITEM_NAME, MAX_UNIT_INPUT, priceMessage, qtyMessage } from "./catalogRules";
import { useWorkspace } from "./context";
import { errorText, FieldError, Loading, LoadMore } from "./parts";

/** How long typing must pause before the catalog is searched again. */
export const PICK_SEARCH_DELAY_MS = 300;
const PICK_PAGE = 20;
const MAX_QUERY_LENGTH = 80;

/** A goods line while it is being typed: the quantity and the price are still text. */
export type DraftLine = {
  /** Identity of the row on the screen; not sent. */
  key: number;
  /** Null for a good typed by name. */
  catalogItemId: string | null;
  name: string;
  /** Empty when a typed good has no unit; the server then counts it in pieces. */
  unit: string;
  qtyText: string;
  priceText: string;
};

export type LineReading =
  | { ok: true; line: NewLine; total: number }
  | { ok: false; qty: QtyProblem | null; price: AmountProblem | null; tooSmall: boolean };

export function readDraft(draft: DraftLine): LineReading {
  const qty = parseQty(draft.qtyText);
  const price = parsePrice(draft.priceText);
  if (!qty.ok || !price.ok) {
    return { ok: false, qty: qty.ok ? null : qty.problem, price: price.ok ? null : price.problem, tooSmall: false };
  }
  const total = lineTotal(qty.thousandths, price.amount);
  if (total === null) {
    return { ok: false, qty: null, price: null, tooSmall: true };
  }
  const line: NewLine =
    draft.catalogItemId !== null
      ? { catalogItemId: draft.catalogItemId, qty: qty.thousandths, unitPrice: price.amount }
      : { name: draft.name, unit: draft.unit === "" ? null : draft.unit, qty: qty.thousandths, unitPrice: price.amount };
  return { ok: true, line, total };
}

export type DraftsReading = {
  readings: LineReading[];
  /** Sum of the lines that can be read, in whole UZS. */
  sum: number;
  /** The lines to send; null while any line cannot be read. */
  lines: NewLine[] | null;
};

export function readDrafts(drafts: readonly DraftLine[]): DraftsReading {
  const readings = drafts.map(readDraft);
  const lines: NewLine[] = [];
  let sum = 0;
  for (const reading of readings) {
    if (reading.ok) {
      sum += reading.total;
      lines.push(reading.line);
    }
  }
  return { readings, sum, lines: lines.length === drafts.length ? lines : null };
}

let serial = 0;

function draftFromItem(item: CatalogItem): DraftLine {
  serial += 1;
  return {
    key: serial,
    catalogItemId: item.id,
    name: item.name,
    unit: item.unit,
    qtyText: "1",
    priceText: String(item.price),
  };
}

/**
 * Adds a catalog item to the lines. Picking a good that is already there adds one more of it instead of
 * a second line, so five loaves are one line and five taps.
 */
export function pickItem(drafts: readonly DraftLine[], item: CatalogItem): DraftLine[] {
  const existing = drafts.find((draft) => draft.catalogItemId === item.id);
  if (!existing) {
    return [...drafts, draftFromItem(item)];
  }
  const qty = parseQty(existing.qtyText);
  if (!qty.ok) {
    return [...drafts];
  }
  return drafts.map((draft) =>
    draft === existing ? { ...draft, qtyText: formatQty(qty.thousandths + QTY_SCALE) } : draft,
  );
}

/** Saved goods of an entry: "Non — 2 dona × 4 000 so'm" and the line total. */
export function GoodsList({ lines }: { lines: readonly GoodsLine[] }) {
  const { t, language } = useI18n();
  return (
    <ul className="goods" aria-label={t("goods.title")}>
      {lines.map((line) => (
        <li key={line.lineNo} className="goods__line">
          <span className="goods__name">{line.name}</span>
          <span className="goods__total">{formatMoney(line.lineTotal, language)}</span>
          <span className="goods__detail">
            {t("goods.line", {
              qty: formatQty(line.qty),
              unit: line.unit,
              price: formatMoney(line.unitPrice, language),
            })}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Picker({ onPick, disabled }: { onPick: (item: CatalogItem) => void; disabled: boolean }) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(text.trim()), PICK_SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [text]);

  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) => api.listCatalog({ q: query, status: "active", cursor, limit: PICK_PAGE }, signal),
    [api, query],
  );

  // The field sits inside the entry form: Enter searches at once and must not save the entry.
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Enter") {
      event.preventDefault();
      setQuery(text.trim());
    }
  };

  return (
    <div className="picker">
      <input
        type="search"
        className="input"
        value={text}
        maxLength={MAX_QUERY_LENGTH}
        autoComplete="off"
        aria-label={t("goods.search")}
        placeholder={t("goods.search")}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
      />
      {state.status === "loading" ? <Loading /> : null}
      {state.status === "error" ? (
        <div className="notice notice--error" role="alert">
          <p>{errorText(state.error, t)}</p>
          <button type="button" className="button" onClick={reload}>
            {t("action.retry")}
          </button>
        </div>
      ) : null}
      {state.status === "ready" && state.items.length === 0 ? <p className="state">{t("goods.pick.none")}</p> : null}
      {state.status === "ready" && state.items.length > 0 ? (
        <>
          <ul className="picks" aria-label={t("goods.pick.list")}>
            {state.items.map((item) => (
              <li key={item.id}>
                <button type="button" className="pick" onClick={() => onPick(item)} disabled={disabled}>
                  <span className="row__name">{item.name}</span>
                  <span className="row__meta">
                    {t("goods.pick.price", { price: formatMoney(item.price, language), unit: item.unit })}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {state.nextCursor !== null ? (
            <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} />
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function TypedGood({ onAdd, disabled }: { onAdd: (draft: DraftLine) => void; disabled: boolean }) {
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [unit, setUnit] = useState("");
  const [price, setPrice] = useState("");
  const [errors, setErrors] = useState<{ name: string | null; price: string | null }>({ name: null, price: null });

  const add = () => {
    const cleanName = cleanItemName(name);
    const parsed = parsePrice(price);
    const found = { name: itemNameProblem(cleanName, t), price: parsed.ok ? null : priceMessage(parsed.problem, t) };
    setErrors(found);
    if (found.name !== null || !parsed.ok) {
      return;
    }
    serial += 1;
    onAdd({
      key: serial,
      catalogItemId: null,
      name: cleanName,
      unit: unit.trim(),
      qtyText: "1",
      priceText: String(parsed.amount),
    });
    setName("");
    setUnit("");
    setPrice("");
  };

  // Inside the entry form: Enter adds the good and must not save the entry.
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Enter") {
      event.preventDefault();
      add();
    }
  };

  return (
    <fieldset className="field typed">
      <legend>{t("goods.new")}</legend>
      <input
        className="input"
        value={name}
        maxLength={MAX_ITEM_NAME * 2}
        autoComplete="off"
        aria-label={t("goods.new.name")}
        placeholder={t("goods.new.name")}
        aria-invalid={errors.name !== null}
        aria-describedby="typed-name-error"
        onChange={(event) => {
          setName(event.target.value);
          setErrors((current) => ({ ...current, name: null }));
        }}
        onKeyDown={onKeyDown}
      />
      <FieldError id="typed-name-error" message={errors.name} />
      <div className="typed__row">
        <input
          className="input"
          value={unit}
          maxLength={MAX_UNIT_INPUT}
          autoComplete="off"
          aria-label={t("goods.new.unit")}
          placeholder={t("goods.new.unit")}
          onChange={(event) => setUnit(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <input
          className="input"
          value={price}
          inputMode="numeric"
          autoComplete="off"
          aria-label={t("goods.new.price")}
          placeholder={t("goods.new.price")}
          aria-invalid={errors.price !== null}
          aria-describedby="typed-price-error"
          onChange={(event) => {
            setPrice(event.target.value);
            setErrors((current) => ({ ...current, price: null }));
          }}
          onKeyDown={onKeyDown}
        />
      </div>
      <FieldError id="typed-price-error" message={errors.price} />
      <button type="button" className="button" onClick={add} disabled={disabled}>
        {t("goods.new.add")}
      </button>
    </fieldset>
  );
}

type GoodsEditorProps = {
  drafts: readonly DraftLine[];
  onChange: (next: DraftLine[]) => void;
  /** After a refused save every unreadable field is marked, including the ones still empty. */
  showAllProblems: boolean;
  /** While the entry is being saved nothing can be added, changed or removed. */
  disabled: boolean;
};

/**
 * The goods of one credit sale: lines picked from the catalog or typed by name, each with a quantity,
 * this sale's price and its total, and the sum of them all. It only edits; the form around it saves.
 */
export function GoodsEditor({ drafts, onChange, showAllProblems, disabled }: GoodsEditorProps) {
  const { t, language } = useI18n();
  const { readings, sum } = readDrafts(drafts);
  const full = drafts.length >= MAX_LINES;

  const change = (key: number, patch: Partial<DraftLine>) =>
    onChange(drafts.map((draft) => (draft.key === key ? { ...draft, ...patch } : draft)));

  return (
    <section className="goods-editor" aria-label={t("goods.title")}>
      {drafts.length > 0 ? (
        <ul className="lines" aria-label={t("goods.lines")}>
          {drafts.map((draft, index) => {
            const reading = readings[index];
            const qtyProblem = reading && !reading.ok ? reading.qty : null;
            const priceProblem = reading && !reading.ok ? reading.price : null;
            // An empty field is not an error while the seller is still on their way to it.
            const qtyError = qtyProblem !== null && (showAllProblems || qtyProblem !== "empty") ? qtyMessage(qtyProblem, t) : null;
            const priceError =
              priceProblem !== null && (showAllProblems || priceProblem !== "empty") ? priceMessage(priceProblem, t) : null;
            const tooSmall = reading && !reading.ok && reading.tooSmall ? t("goods.line.tooSmall") : null;
            return (
              <li key={draft.key} className="line">
                <p className="line__head">
                  <span className="row__name">{draft.name}</span>
                  <button
                    type="button"
                    className="button button--small"
                    aria-label={t("goods.remove", { name: draft.name })}
                    onClick={() => onChange(drafts.filter((other) => other.key !== draft.key))}
                    disabled={disabled}
                  >
                    ×
                  </button>
                </p>
                <div className="line__fields">
                  <input
                    className="input line__qty"
                    inputMode="decimal"
                    autoComplete="off"
                    value={draft.qtyText}
                    aria-label={t("goods.qty", { name: draft.name })}
                    aria-invalid={qtyError !== null}
                    disabled={disabled}
                    onChange={(event) => change(draft.key, { qtyText: event.target.value })}
                  />
                  <span className="line__unit">{draft.unit} ×</span>
                  <input
                    className="input line__price"
                    inputMode="numeric"
                    autoComplete="off"
                    value={draft.priceText}
                    aria-label={t("goods.unitPrice", { name: draft.name })}
                    aria-invalid={priceError !== null}
                    disabled={disabled}
                    onChange={(event) => change(draft.key, { priceText: event.target.value })}
                  />
                </div>
                <p className="line__total">{reading?.ok ? formatMoney(reading.total, language) : "—"}</p>
                <FieldError id={`line-${draft.key}-qty-error`} message={qtyError} />
                <FieldError id={`line-${draft.key}-price-error`} message={priceError} />
                <FieldError id={`line-${draft.key}-total-error`} message={tooSmall} />
              </li>
            );
          })}
        </ul>
      ) : null}
      {drafts.length > 0 ? (
        <p className="balance goods-editor__sum">
          <span>{t("goods.sum")}</span> <strong>{formatMoney(sum, language)}</strong>
        </p>
      ) : null}
      {/* Before the first good: what the two ways below are for. */}
      {drafts.length === 0 ? <p className="state">{t("goods.lines.none")}</p> : null}
      {full ? (
        <p className="hint">{t("goods.max", { max: MAX_LINES })}</p>
      ) : (
        <>
          <Picker onPick={(item) => onChange(pickItem(drafts, item))} disabled={disabled} />
          <TypedGood onAdd={(draft) => onChange([...drafts, draft])} disabled={disabled} />
        </>
      )}
    </section>
  );
}
