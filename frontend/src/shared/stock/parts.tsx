import { type ReactNode, useMemo, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { Language, MessageKey } from "../../i18n/types";
import type { ApiError } from "../api";
import { formatMoney } from "../format";
import { type Loaded, useLoad } from "../hooks";
import { type TableProps, useDesktop } from "../layout";
import type { Currency } from "../money";
import { useWorkspace } from "../workspace/context";
import { errorText, FieldError } from "../workspace/parts";
import { stockQtyText } from "../workspace/StockNotes";
import "./messages";
import { MAX_REASON, tidy } from "./quantity";
import "./stock.css";
import { type Labelled, type StockApi, stockOf, type StockSettings } from "./stockApi";

export const NONE = "—";

/** The stock's calls for the active shop. */
export function useStock(): StockApi {
  const { api } = useWorkspace();
  return useMemo(() => stockOf(api), [api]);
}

/** The units, the write-off reasons and the currencies the shop buys in. */
export function useStockSettings(): { state: Loaded<StockSettings>; reload: () => void } {
  const stock = useStock();
  return useLoad((signal) => stock.settings(signal), [stock]);
}

/** The server's own word for a unit or a reason, in the reader's language; the key when it has none. */
export function labelOf(list: readonly Labelled[] | undefined, key: string | null, language: Language): string {
  if (key === null) {
    return NONE;
  }
  return list?.find((entry) => entry.key === key)?.label[language] ?? key;
}

/** "7,5 kg": a quantity of the API with its unit. */
export function qtyWithUnit(qty: string, unit: string): string {
  return unit === "" ? stockQtyText(qty) : `${stockQtyText(qty)} ${unit}`;
}

/** An amount in its own currency, or a dash for none. */
export function moneyOrNone(amount: number | null | undefined, language: Language, currency: Currency | null | undefined) {
  return amount === null || amount === undefined ? NONE : formatMoney(amount, language, currency ?? "UZS");
}

/**
 * A list of the stock: a real table on a wide screen of the web panel, and rows on a phone, where each
 * column is a line with its name before the value. One description of the columns serves both.
 */
export function Listing<T>(props: TableProps<T>) {
  const desktop = useDesktop();
  if (desktop) {
    return <desktop.Table {...props} />;
  }
  const { caption, columns, items, rowKey, expanded } = props;
  return (
    <ul className="rows" aria-label={caption}>
      {items.map((item) => (
        <li key={rowKey(item)} className="row">
          {columns.map((column) => {
            const content = column.cell(item);
            if (content === null || content === undefined || content === false || content === "") {
              return null;
            }
            return column.rowHeader ? (
              <p key={column.id} className="row__name">
                {content}
              </p>
            ) : (
              <p key={column.id} className="row__meta stock-fact">
                <span>{column.header}</span>
                <span>{content}</span>
              </p>
            );
          })}
          {expanded?.(item) ?? null}
        </li>
      ))}
    </ul>
  );
}

/** One fact of a page: its name and its value, as a pair of a description list. */
export function Fact({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className="stock-fact">
      <dt>{name}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/**
 * Why something is taken back: a document or an entry of a supplier's account is cancelled with a
 * reason, 1 to 200 characters, which stays in the books. Nothing is sent without one.
 */
export function CancelForm({
  id,
  label,
  submitLabel,
  pending,
  error,
  onSubmit,
  onClose,
}: {
  id: string;
  label: string;
  submitLabel: string;
  pending: boolean;
  error: ApiError | null;
  onSubmit: (reason: string) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const send = () => {
    const reason = tidy(text);
    if (reason === null || [...reason].length > MAX_REASON) {
      setProblem(t("stock.reason.invalid", { max: MAX_REASON }));
      return;
    }
    onSubmit(reason);
  };
  return (
    <div className="form notice" role="group" aria-label={label}>
      {error ? (
        <p className="field__error" role="alert">
          {errorText(error, t)}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor={id}>{label}</label>
        <textarea
          id={id}
          className="input input--text"
          rows={2}
          value={text}
          maxLength={400}
          aria-invalid={problem !== null}
          aria-describedby={`${id}-error`}
          onChange={(event) => {
            setText(event.target.value);
            setProblem(null);
          }}
        />
        <FieldError id={`${id}-error`} message={problem} />
      </div>
      <p className="actions">
        <button type="button" className="button button--danger" onClick={send} disabled={pending}>
          {pending ? t("state.saving") : submitLabel}
        </button>
        <button type="button" className="button" onClick={onClose} disabled={pending}>
          {t("action.close")}
        </button>
      </p>
    </div>
  );
}

export const DOCUMENT_KIND_LABELS = {
  receipt: "stock.kind.receipt",
  customer_return: "stock.kind.customer_return",
  supplier_return: "stock.kind.supplier_return",
  write_off: "stock.kind.write_off",
  stocktake: "stock.kind.stocktake",
} as const satisfies Record<string, MessageKey>;

export const DOCUMENT_STATUS_LABELS = {
  draft: "stock.status.draft",
  posted: "stock.status.posted",
  cancelled: "stock.status.cancelled",
} as const satisfies Record<string, MessageKey>;

const MOVEMENT_KIND_LABELS: Readonly<Record<string, MessageKey>> = {
  receipt: "stock.move.receipt",
  sale: "stock.move.sale",
  customer_return: "stock.move.customer_return",
  supplier_return: "stock.move.supplier_return",
  write_off: "stock.move.write_off",
  correction: "stock.move.correction",
  reversal: "stock.move.reversal",
};

const SUPPLIER_ENTRY_LABELS: Readonly<Record<string, MessageKey>> = {
  purchase: "supplier.entry.purchase",
  opening: "supplier.entry.opening",
  payment: "supplier.entry.payment",
  return: "supplier.entry.return",
  reversal: "supplier.entry.reversal",
};

/** The catalog's word for a kind, or the server's own name for one this client does not know yet. */
export function kindText(kind: string, of: "movement" | "entry", t: (key: MessageKey) => string): string {
  const key = (of === "movement" ? MOVEMENT_KIND_LABELS : SUPPLIER_ENTRY_LABELS)[kind];
  return key ? t(key) : kind;
}

/** The permission a kind of document needs: receiving goods is one job, correcting the books another. */
export function permissionOfKind(kind: string): "stock.receive" | "stock.adjust" {
  return kind === "receipt" || kind === "supplier_return" ? "stock.receive" : "stock.adjust";
}
