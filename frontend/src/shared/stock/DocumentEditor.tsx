import { useEffect, useRef, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { MessageKey } from "../../i18n/types";
import type { ApiError, Customer } from "../api";
import { formatMoney, tashkentDay } from "../format";
import { parsePrice } from "../goods";
import { useLoad, useSubmit } from "../hooks";
import { type Currency, formatUzs } from "../money";
import { addDays, toIsoDate } from "../promise";
import { useMay, useWorkspace } from "../workspace/context";
import { CurrencyToggle, errorText, FieldError } from "../workspace/parts";
import { stockQtyText } from "../workspace/StockNotes";
import { readBarcode, type ScanHost } from "./barcode";
import {
  buildDocument,
  currencyOfDraft,
  type DocumentDraft,
  type DraftLine,
  draftOf,
  type DraftProblems,
  draftTotal,
  emptyDraft,
  lineTotalOf,
  type NewItem,
  oneMore,
  type Payment,
  PRICED_KINDS,
  SUPPLIER_KINDS,
} from "./documentDraft";
import { ItemFinder } from "./ItemFinder";
import { DOCUMENT_KIND_LABELS, labelOf, MethodChoice, useStock } from "./parts";
import { MAX_DOCUMENT_LINES, MAX_NOTE, tidy } from "./quantity";
import { labelIn } from "./stockApi";
import type { NewDocument, PaymentMethod, StockDocument, StockDocumentKind, StockItem, StockSettings } from "./stockApi";

const OLDEST_DAYS = 365;
const QTY = /^-?\d{1,12}(?:\.\d{1,3})?$/;

const FIELD_TEXT: Readonly<Record<string, MessageKey>> = {
  paid: "stock.doc.refused.paid",
  doc_date: "stock.doc.problem.date",
  supplier_id: "stock.doc.problem.supplier",
  customer_id: "stock.doc.problem.customer",
  reason: "stock.doc.problem.reason",
  currency: "stock.doc.refused.currency",
};

/**
 * A refusal of a document in words: the server's own sentence, and what the catalog can add to it from
 * the refusal's fields: which line, which item, how much is on hand.
 */
export function DocumentRefusal({ error }: { error: ApiError }) {
  const { t } = useI18n();
  const details: string[] = [];
  const lines = new Set<number>();
  for (const name of Object.keys(error.fields)) {
    const line = /^lines\.(\d+)\./.exec(name);
    if (line) {
      lines.add(Number(line[1]) + 1);
    } else if (error.code === "VALIDATION" && FIELD_TEXT[name]) {
      details.push(t(FIELD_TEXT[name]));
    }
  }
  const position = error.fields["line"];
  if (position !== undefined && /^\d{1,3}$/.test(position)) {
    lines.add(Number(position) + 1);
  }
  for (const line of [...lines].sort((a, b) => a - b)) {
    details.push(t("stock.doc.refused.line", { line }));
  }
  const { name, on_hand: onHand, wanted, code } = error.fields;
  if (error.code === "STOCK_INSUFFICIENT" && name !== undefined && onHand !== undefined && QTY.test(onHand)) {
    details.push(
      wanted !== undefined && QTY.test(wanted)
        ? t("stock.doc.insufficient", { name, onHand: stockQtyText(onHand), wanted: stockQtyText(wanted) })
        : t("stock.doc.onHand", { name, onHand: stockQtyText(onHand) }),
    );
  }
  if (error.code === "STOCK_ALREADY_USED" && name !== undefined && onHand !== undefined && QTY.test(onHand)) {
    details.push(t("stock.doc.onHand", { name, onHand: stockQtyText(onHand) }));
  }
  if (error.code === "BARCODE_TAKEN" && code !== undefined) {
    details.push(t("stock.doc.refused.barcode", { code }));
  }
  return (
    <div className="notice notice--error" role="alert">
      <p>{errorText(error, t)}</p>
      {details.map((detail) => (
        <p key={detail}>{detail}</p>
      ))}
    </div>
  );
}

/** A good met for the first time, on a receipt: it joins the catalog, counted from the start. */
function NewItemForm({
  settings,
  barcode,
  onAdd,
  onClose,
}: {
  settings: StockSettings;
  /** The code that found nothing, when that is how the form was opened. */
  barcode: string;
  onAdd: (item: NewItem) => void;
  onClose: () => void;
}) {
  const { t, language } = useI18n();
  const [name, setName] = useState("");
  const [unit, setUnit] = useState(settings.units[0]?.key ?? "dona");
  const [price, setPrice] = useState("");
  const [code, setCode] = useState(barcode);
  const [problems, setProblems] = useState<{ name: boolean; price: boolean; code: boolean }>({ name: false, price: false, code: false });

  const add = () => {
    const cleanName = tidy(name);
    const read = parsePrice(price);
    const typed = code.trim();
    const checked = typed === "" ? null : readBarcode(typed);
    const found = {
      name: cleanName === null || [...cleanName].length > 80,
      price: !read.ok,
      code: checked !== null && !checked.ok,
    };
    setProblems(found);
    if (cleanName === null || !read.ok || found.name || found.code) {
      return;
    }
    onAdd({ name: cleanName, unit, price: read.amount, barcode: checked !== null && checked.ok ? checked.code : null });
  };

  return (
    <div className="form notice" role="group" aria-label={t("stock.new.title")}>
      <div className="field">
        <label htmlFor="stock-new-name">{t("stock.new.name")}</label>
        <input
          id="stock-new-name"
          className="input"
          value={name}
          maxLength={200}
          autoComplete="off"
          aria-invalid={problems.name}
          aria-describedby="stock-new-name-error"
          onChange={(event) => setName(event.target.value)}
        />
        <FieldError id="stock-new-name-error" message={problems.name ? t("stock.new.name.invalid") : null} />
      </div>
      <div className="typed__row">
        <div className="field">
          <label htmlFor="stock-new-unit">{t("stock.item.unit")}</label>
          <select id="stock-new-unit" className="input" value={unit} onChange={(event) => setUnit(event.target.value)}>
            {settings.units.map((known) => (
              <option key={known.key} value={known.key}>
                {labelIn(known.label, language)}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="stock-new-price">{t("stock.new.price")}</label>
          <input
            id="stock-new-price"
            className="input"
            inputMode="numeric"
            autoComplete="off"
            value={price}
            aria-invalid={problems.price}
            aria-describedby="stock-new-price-error"
            onChange={(event) => setPrice(event.target.value)}
          />
          <FieldError id="stock-new-price-error" message={problems.price ? t("stock.new.price.invalid") : null} />
        </div>
      </div>
      <div className="field">
        <label htmlFor="stock-new-code">{t("stock.new.barcode")}</label>
        <input
          id="stock-new-code"
          className="input"
          value={code}
          maxLength={100}
          autoComplete="off"
          aria-invalid={problems.code}
          aria-describedby="stock-new-code-error"
          onChange={(event) => setCode(event.target.value)}
        />
        <FieldError id="stock-new-code-error" message={problems.code ? t("stock.scan.problem.check") : null} />
      </div>
      <p className="actions">
        <button type="button" className="button button--primary" onClick={add}>
          {t("stock.new.add")}
        </button>
        <button type="button" className="button" onClick={onClose}>
          {t("action.cancel")}
        </button>
      </p>
    </div>
  );
}

/** The customer whose goods came back: found by name or phone, chosen once. */
function CustomerChoice({
  chosen,
  problem,
  onChoose,
}: {
  chosen: { id: string; name: string } | null;
  problem: boolean;
  onChoose: (customer: { id: string; name: string } | null) => void;
}) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const found = useLoad(
    (signal) => (query === "" ? Promise.resolve<Customer[]>([]) : api.listCustomers({ q: query, limit: 8 }, signal).then((page) => page.items)),
    [api, query],
  );
  if (chosen !== null) {
    return (
      <p className="balance">
        <span>{t("stock.doc.customer")}</span> <strong>{chosen.name}</strong>{" "}
        <button type="button" className="button button--small" onClick={() => onChoose(null)}>
          {t("stock.doc.change")}
        </button>
      </p>
    );
  }
  return (
    <div className="field">
      <label htmlFor="stock-doc-customer">{t("stock.doc.customer")}</label>
      <div className="search">
        <input
          id="stock-doc-customer"
          className="input"
          value={text}
          maxLength={80}
          autoComplete="off"
          aria-invalid={problem}
          aria-describedby="stock-doc-customer-error"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              setQuery(text.trim());
            }
          }}
        />
        <button type="button" className="button" onClick={() => setQuery(text.trim())}>
          {t("action.search")}
        </button>
      </div>
      <FieldError id="stock-doc-customer-error" message={problem ? t("stock.doc.problem.customer") : null} />
      {query !== "" && found.state.status === "error" ? (
        <p className="field__error" role="alert">
          {errorText(found.state.error, t)}
        </p>
      ) : null}
      {query !== "" && found.state.status === "ready" ? (
        found.state.data.length === 0 ? (
          <p className="state">{t("customers.noMatch")}</p>
        ) : (
          <ul className="picks" aria-label={t("stock.doc.customer.list")}>
            {found.state.data.map((customer) => (
              <li key={customer.id}>
                <button
                  type="button"
                  className="pick"
                  onClick={() => onChoose({ id: customer.id, name: customer.displayName })}
                >
                  <span>{customer.displayName}</span>
                  <span>{customer.phone ?? ""}</span>
                </button>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </div>
  );
}

const NO_PROBLEMS: DraftProblems = {
  lines: {},
  empty: false,
  tooMany: false,
  total: false,
  date: false,
  supplier: false,
  customer: false,
  reason: false,
  paid: false,
  note: false,
};

const PAYMENTS: readonly Payment[] = ["full", "credit", "part"];
const PAYMENT_LABELS: Readonly<Record<Payment, MessageKey>> = {
  full: "stock.pay.full",
  credit: "stock.pay.credit",
  part: "stock.pay.part",
};

type Job = { document: NewDocument; post: boolean };

/**
 * The form of a stock document, of any of the five kinds: a new one, or a stored draft being changed.
 * It is saved as a draft or, where the kind allows, posted in the same step; a stocktake is always
 * saved first, because its differences are what the person must see before it takes effect.
 */
export function DocumentEditor({
  kind,
  settings,
  initial,
  host,
  onSaved,
  onClose,
}: {
  kind: StockDocumentKind;
  settings: StockSettings;
  initial?: StockDocument | undefined;
  host?: ScanHost | undefined;
  onSaved: (document: StockDocument) => void;
  onClose?: (() => void) | undefined;
}) {
  const { now } = useWorkspace();
  const { t, language } = useI18n();
  const can = useMay();
  const stock = useStock();
  const today = tashkentDay(now());
  const [draft, setDraft] = useState<DocumentDraft>(
    () => (initial ? draftOf(initial) : null) ?? emptyDraft(kind, toIsoDate(today)),
  );
  const [customer, setCustomer] = useState<{ id: string; name: string } | null>(initial?.customer ?? null);
  const [problems, setProblems] = useState<DraftProblems>(NO_PROBLEMS);
  const [adding, setAdding] = useState<{ barcode: string } | null>(null);
  // Asked only in a shop that keeps a cash book; elsewhere nothing is asked and nothing is sent.
  const [method, setMethod] = useState<PaymentMethod>("cash");
  const nextKey = useRef(draft.lines.length + 1);

  const priced = PRICED_KINDS.has(kind);
  const withSupplier = SUPPLIER_KINDS.has(kind);
  const currency = currencyOfDraft(draft);
  const mayPay = can("suppliers.pay");
  // Only those who may read the suppliers are given the list; a receipt needs none.
  const suppliers = useLoad(
    (signal) =>
      withSupplier && can("suppliers.view")
        ? stock.suppliers({ status: "active", limit: 100 }, signal).then((page) => page.suppliers)
        : Promise.resolve([]),
    [stock, withSupplier],
  );
  const save = useSubmit(async (job: Job, key): Promise<StockDocument> => {
    if (initial === undefined) {
      return stock.createDocument(job.document, job.post, key);
    }
    const stored = await stock.updateDocument(initial.id, job.document, key);
    return job.post ? stock.postDocument(stored.id, `${key}-post`) : stored;
  });
  const pending = save.state.status === "pending";
  const saved = useRef(onSaved);
  saved.current = onSaved;
  useEffect(() => {
    if (save.state.status === "done") {
      saved.current(save.state.result);
    }
  }, [save.state]);

  const change = (patch: Partial<DocumentDraft>) => {
    setDraft((current) => ({ ...current, ...patch }));
    setProblems(NO_PROBLEMS);
  };
  const changeLine = (key: number, patch: Partial<DraftLine>) => {
    setDraft((current) => ({
      ...current,
      lines: current.lines.map((line) => (line.key === key ? { ...line, ...patch } : line)),
    }));
    setProblems(NO_PROBLEMS);
  };

  const pick = (item: StockItem) => {
    setAdding(null);
    setProblems(NO_PROBLEMS);
    setDraft((current) => {
      // A second scan of the same label is one more of it, not a second line.
      const existing = current.lines.find((line) => line.item?.id === item.id);
      if (existing) {
        return {
          ...current,
          lines: current.lines.map((line) => (line === existing ? { ...line, qty: oneMore(line.qty) } : line)),
        };
      }
      let cost = "";
      if (kind === "customer_return") {
        // Goods come back at the price they are sold for; the person corrects it when it was another.
        cost = formatUzs(item.price);
      }
      const line: DraftLine = {
        key: nextKey.current++,
        item: { id: item.id, name: item.name, unit: item.unit },
        added: null,
        qty: "1",
        cost,
      };
      return { ...current, lines: [...current.lines, line] };
    });
  };

  const send = (post: boolean) => {
    const built = buildDocument({
      ...draft,
      customerId: customer?.id ?? null,
      // Without the permission to pay a supplier the goods are received on credit, whatever was chosen before.
      payment: mayPay ? draft.payment : "credit",
      method: settings.cashBook ? method : null,
    });
    if (!built.ok) {
      setProblems(built.problems);
      return;
    }
    save.submit({ document: built.document, post });
  };

  const total = draftTotal(draft);
  const knownSuppliers = suppliers.state.status === "ready" ? suppliers.state.data : [];
  const supplierOptions =
    initial?.supplier && !knownSuppliers.some((known) => known.id === initial.supplier?.id)
      ? [{ id: initial.supplier.id, name: initial.supplier.name }, ...knownSuppliers]
      : knownSuppliers;
  const minDate = toIsoDate(addDays(today, -OLDEST_DAYS));
  const full = draft.lines.length >= MAX_DOCUMENT_LINES;
  const mayAddNew = kind === "receipt";

  return (
    <div className="form">
      <h2 className="subject">
        {initial
          ? t("stock.doc.ref", { kind: t(DOCUMENT_KIND_LABELS[kind]), number: initial.number })
          : t(DOCUMENT_KIND_LABELS[kind])}
      </h2>
      {kind === "stocktake" ? <p className="hint">{t("stock.take.hint")}</p> : null}
      {save.state.status === "error" ? <DocumentRefusal error={save.state.error} /> : null}

      <div className="field">
        <label htmlFor="stock-doc-date">{t("stock.doc.date")}</label>
        <input
          id="stock-doc-date"
          type="date"
          className="input"
          value={draft.docDate}
          min={minDate}
          max={toIsoDate(today)}
          disabled={pending}
          aria-invalid={problems.date}
          aria-describedby="stock-doc-date-error"
          onChange={(event) => change({ docDate: event.target.value })}
        />
        <FieldError id="stock-doc-date-error" message={problems.date ? t("stock.doc.problem.date") : null} />
      </div>

      {withSupplier && supplierOptions.length > 0 ? (
        <div className="field">
          <label htmlFor="stock-doc-supplier">{t("stock.doc.supplier")}</label>
          <select
            id="stock-doc-supplier"
            className="input"
            value={draft.supplierId ?? ""}
            disabled={pending}
            aria-invalid={problems.supplier}
            aria-describedby="stock-doc-supplier-error"
            onChange={(event) => change({ supplierId: event.target.value === "" ? null : event.target.value })}
          >
            <option value="">{t(kind === "receipt" ? "stock.doc.supplier.none" : "stock.doc.supplier.choose")}</option>
            {supplierOptions.map((known) => (
              <option key={known.id} value={known.id}>
                {known.name}
              </option>
            ))}
          </select>
          <FieldError id="stock-doc-supplier-error" message={problems.supplier ? t("stock.doc.problem.supplier") : null} />
        </div>
      ) : null}
      {kind === "supplier_return" && supplierOptions.length === 0 && suppliers.state.status === "ready" ? (
        <p className="notice notice--warning">{t("stock.doc.supplier.missing")}</p>
      ) : null}

      {kind === "customer_return" ? (
        <CustomerChoice
          chosen={customer}
          problem={problems.customer}
          onChoose={(chosen) => {
            setCustomer(chosen);
            setProblems(NO_PROBLEMS);
          }}
        />
      ) : null}

      {withSupplier && settings.currencies.includes("USD") ? (
        <CurrencyToggle
          value={draft.currency}
          disabled={pending}
          // A cost typed in one currency is not a cost of the other.
          onChange={(next: Currency) =>
            change({ currency: next, paidText: "", lines: draft.lines.map((line) => ({ ...line, cost: "" })) })
          }
        />
      ) : null}

      {kind === "write_off" ? (
        <div className="field">
          <label htmlFor="stock-doc-reason">{t("stock.doc.reason")}</label>
          <select
            id="stock-doc-reason"
            className="input"
            value={draft.reason ?? ""}
            disabled={pending}
            aria-invalid={problems.reason}
            aria-describedby="stock-doc-reason-error"
            onChange={(event) => change({ reason: event.target.value === "" ? null : event.target.value })}
          >
            <option value="">{t("stock.doc.reason.choose")}</option>
            {settings.writeOffReasons.map((reason) => (
              <option key={reason.key} value={reason.key}>
                {labelIn(reason.label, language)}
              </option>
            ))}
          </select>
          <FieldError id="stock-doc-reason-error" message={problems.reason ? t("stock.doc.problem.reason") : null} />
        </div>
      ) : null}

      <h3 className="section-label">{t("stock.doc.lines")}</h3>
      {draft.lines.length === 0 ? <p className="state">{t("stock.doc.lines.none")}</p> : null}
      <ul className="lines" aria-label={t("stock.doc.lines")}>
        {draft.lines.map((line) => {
          const name = line.item?.name ?? line.added?.name ?? "";
          const unit = line.item?.unit ?? line.added?.unit ?? "";
          const bad = problems.lines[line.key];
          const sum = priced ? lineTotalOf(line, currency) : null;
          return (
            <li key={line.key} className="line">
              <p className="line__head">
                <span className="row__name">{name}</span>{" "}
                {line.added !== null ? <span className="row__meta">{t("stock.new.badge")}</span> : null}
              </p>
              <div className="stock-line">
                <div className="field">
                  <label htmlFor={`stock-line-qty-${line.key}`}>
                    {t(kind === "stocktake" ? "stock.line.counted" : "stock.line.qty", {
                      unit: labelOf(settings.units, unit, language),
                    })}
                  </label>
                  <input
                    id={`stock-line-qty-${line.key}`}
                    className="input"
                    inputMode="decimal"
                    autoComplete="off"
                    value={line.qty}
                    disabled={pending}
                    aria-invalid={bad?.qty === true}
                    aria-describedby={`stock-line-error-${line.key}`}
                    onChange={(event) => changeLine(line.key, { qty: event.target.value })}
                  />
                </div>
                {priced ? (
                  <div className="field">
                    <label htmlFor={`stock-line-cost-${line.key}`}>
                      {t(kind === "customer_return" ? "stock.line.price" : "stock.line.cost")}
                    </label>
                    <input
                      id={`stock-line-cost-${line.key}`}
                      className="input"
                      inputMode={currency === "USD" ? "decimal" : "numeric"}
                      autoComplete="off"
                      value={line.cost}
                      disabled={pending}
                      aria-invalid={bad?.cost === true}
                      aria-describedby={`stock-line-error-${line.key}`}
                      onChange={(event) => changeLine(line.key, { cost: event.target.value })}
                    />
                  </div>
                ) : null}
                <button
                  type="button"
                  className="button button--small"
                  disabled={pending}
                  aria-label={t("stock.line.remove", { name })}
                  onClick={() => change({ lines: draft.lines.filter((kept) => kept.key !== line.key) })}
                >
                  {t("action.delete")}
                </button>
              </div>
              {sum !== null ? <p className="row__meta">{formatMoney(sum, language, currency)}</p> : null}
              <FieldError
                id={`stock-line-error-${line.key}`}
                message={
                  bad === undefined
                    ? null
                    : bad.qty
                      ? t(kind === "stocktake" ? "stock.qty.invalid" : "stock.qty.positive")
                      : t(currency === "USD" ? "stock.cost.invalid.usd" : "stock.cost.invalid")
                }
              />
            </li>
          );
        })}
      </ul>
      {problems.empty ? (
        <p className="field__error" role="alert">
          {t("stock.doc.problem.empty")}
        </p>
      ) : null}
      {problems.tooMany || full ? <p className="hint">{t("stock.doc.problem.tooMany", { max: MAX_DOCUMENT_LINES })}</p> : null}

      {full ? null : (
        <ItemFinder
          id="stock-doc-find"
          // A receipt may take any good of the catalog and starts counting it; the rest move counted goods only.
          filter={kind === "receipt" ? "all" : "tracked"}
          disabled={pending}
          host={host}
          onPick={pick}
          missing={(code) =>
            mayAddNew && adding === null ? (
              <p className="actions">
                <button type="button" className="button" onClick={() => setAdding({ barcode: code })}>
                  {t("stock.new.withCode")}
                </button>
              </p>
            ) : null
          }
        />
      )}
      {mayAddNew && adding === null && !full ? (
        <p className="actions">
          <button type="button" className="button" disabled={pending} onClick={() => setAdding({ barcode: "" })}>
            {t("stock.new.open")}
          </button>
        </p>
      ) : null}
      {mayAddNew && adding !== null ? (
        <NewItemForm
          key={adding.barcode}
          settings={settings}
          barcode={adding.barcode}
          onClose={() => setAdding(null)}
          onAdd={(added) => {
            setAdding(null);
            change({
              lines: [...draft.lines, { key: nextKey.current++, item: null, added, qty: "1", cost: "" }],
            });
          }}
        />
      ) : null}

      {priced ? (
        <p className="balance">
          <span>{t("stock.doc.total")}</span> <strong>{formatMoney(total, language, currency)}</strong>
        </p>
      ) : null}
      {problems.total ? (
        <p className="field__error" role="alert">
          {t("stock.doc.problem.total")}
        </p>
      ) : null}

      {kind === "receipt" && draft.supplierId === null ? <p className="hint">{t("stock.pay.cash")}</p> : null}
      {kind === "receipt" && draft.supplierId !== null ? (
        <fieldset className="field choices">
          <legend>{t("stock.pay.legend")}</legend>
          {/* Paying a supplier is a permission of its own: without it the goods are received on credit. */}
          {PAYMENTS.filter((option) => mayPay || option === "credit").map((option) => (
            <label key={option} className="choice">
              <input
                type="radio"
                name="stock-payment"
                value={option}
                checked={(mayPay ? draft.payment : "credit") === option}
                disabled={pending}
                onChange={() => change({ payment: option })}
              />
              <span>{t(PAYMENT_LABELS[option])}</span>
            </label>
          ))}
          {mayPay ? null : <p className="field__hint">{t("stock.pay.notAllowed")}</p>}
        </fieldset>
      ) : null}
      {(kind === "receipt" && draft.supplierId !== null && mayPay && draft.payment === "part") || kind === "customer_return" ? (
        <div className="field">
          <label htmlFor="stock-doc-paid">{t(kind === "receipt" ? "stock.pay.amount" : "stock.return.paid")}</label>
          <input
            id="stock-doc-paid"
            className="input"
            inputMode={currency === "USD" ? "decimal" : "numeric"}
            autoComplete="off"
            value={draft.paidText}
            disabled={pending}
            aria-invalid={problems.paid}
            aria-describedby="stock-doc-paid-error stock-doc-paid-hint"
            onChange={(event) => change({ paidText: event.target.value })}
          />
          {kind === "customer_return" ? (
            <p className="field__hint" id="stock-doc-paid-hint">
              {t("stock.return.paid.hint")}
            </p>
          ) : null}
          <FieldError id="stock-doc-paid-error" message={problems.paid ? t("stock.doc.refused.paid") : null} />
        </div>
      ) : null}

      {settings.cashBook &&
      ((kind === "receipt" && (draft.supplierId === null || (mayPay && draft.payment !== "credit"))) || kind === "customer_return") ? (
        <MethodChoice id="stock-doc-method" value={method} disabled={pending} onChange={setMethod} />
      ) : null}

      <div className="field">
        <label htmlFor="stock-doc-note">{t("stock.doc.note")}</label>
        <input
          id="stock-doc-note"
          className="input"
          value={draft.note}
          maxLength={400}
          autoComplete="off"
          disabled={pending}
          aria-invalid={problems.note}
          aria-describedby="stock-doc-note-error"
          onChange={(event) => change({ note: event.target.value })}
        />
        <FieldError id="stock-doc-note-error" message={problems.note ? t("stock.doc.problem.note", { max: MAX_NOTE }) : null} />
      </div>

      <p className="actions">
        {kind === "stocktake" ? (
          <button type="button" className="button button--primary" onClick={() => send(false)} disabled={pending}>
            {pending ? t("state.saving") : t("stock.take.save")}
          </button>
        ) : (
          <>
            <button type="button" className="button button--primary" onClick={() => send(true)} disabled={pending}>
              {pending ? t("state.saving") : t("stock.doc.post")}
            </button>
            <button type="button" className="button" onClick={() => send(false)} disabled={pending}>
              {t("stock.doc.saveDraft")}
            </button>
          </>
        )}
        {onClose ? (
          <button type="button" className="button" onClick={onClose} disabled={pending}>
            {t("action.cancel")}
          </button>
        ) : null}
      </p>
    </div>
  );
}
