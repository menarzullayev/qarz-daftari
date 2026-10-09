import { type ReactNode, useCallback, useRef, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { type ApiError, isAbort, toApiError } from "../api";
import { useLoad } from "../hooks";
import { errorText } from "../workspace/parts";
import type { ScanHost } from "./barcode";
import { qtyWithUnit, useStock } from "./parts";
import { ScanField } from "./ScanField";
import type { ItemFilter, StockItem } from "./stockApi";

export type Lookup =
  | { status: "idle" }
  | { status: "pending"; code: string }
  | { status: "found"; code: string; item: StockItem }
  /** The server knows no item with this code: the one answer that offers attaching or adding it. */
  | { status: "missing"; code: string }
  | { status: "error"; code: string; error: ApiError };

/**
 * Looks a barcode up. Only the newest code counts: a slow answer to an earlier scan is dropped, so two
 * quick scans never show the first item under the second code.
 */
export function useLookup(onFound?: (item: StockItem, code: string) => void): {
  state: Lookup;
  lookup: (code: string) => void;
  reset: () => void;
} {
  const stock = useStock();
  const [state, setState] = useState<Lookup>({ status: "idle" });
  const current = useRef<AbortController | null>(null);
  const found = useRef(onFound);
  found.current = onFound;

  const lookup = useCallback(
    (code: string) => {
      current.current?.abort();
      const controller = new AbortController();
      current.current = controller;
      setState({ status: "pending", code });
      stock.lookup(code, controller.signal).then(
        (item) => {
          if (!controller.signal.aborted) {
            setState({ status: "found", code, item });
            found.current?.(item, code);
          }
        },
        (error: unknown) => {
          if (controller.signal.aborted || isAbort(error)) {
            return;
          }
          const failure = toApiError(error);
          setState(failure.status === 404 ? { status: "missing", code } : { status: "error", code, error: failure });
        },
      );
    },
    [stock],
  );
  const reset = useCallback(() => {
    current.current?.abort();
    setState({ status: "idle" });
  }, []);
  return { state, lookup, reset };
}

/** What a lookup that did not end in an item says: still looking, not found (with the code), or failed. */
export function LookupNotice({ state, missing }: { state: Lookup; missing?: ((code: string) => ReactNode) | undefined }) {
  const { t } = useI18n();
  if (state.status === "pending") {
    return (
      <p className="state" role="status">
        {t("stock.scan.looking", { code: state.code })}
      </p>
    );
  }
  if (state.status === "missing") {
    return (
      <div className="notice notice--warning" role="alert">
        <p>{t("stock.scan.missing", { code: state.code })}</p>
        {missing?.(state.code) ?? null}
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <p className="notice notice--error" role="alert">
        {errorText(state.error, t)}
      </p>
    );
  }
  return null;
}

/**
 * Finds an item for a line of a document: by a scanned or typed barcode, or by a part of its name. A
 * found code picks its item at once, which is what a scanner at the counter needs; a name shows the
 * matching items to choose from.
 */
export function ItemFinder({
  id,
  filter,
  disabled = false,
  onPick,
  missing,
  host,
}: {
  id: string;
  /** Which items a name search offers: every catalog item for a receipt, the counted ones otherwise. */
  filter: ItemFilter;
  disabled?: boolean;
  onPick: (item: StockItem) => void;
  /** What to offer under "not found", given the code: adding a new item, where the member may. */
  missing?: ((code: string) => ReactNode) | undefined;
  host?: ScanHost | undefined;
}) {
  const { t } = useI18n();
  const stock = useStock();
  const [words, setWords] = useState("");
  const { state, lookup, reset } = useLookup((item) => onPick(item));
  const matches = useLoad(
    (signal) => (words === "" ? Promise.resolve(null) : stock.items({ q: words, filter, limit: 20 }, signal)),
    [stock, words, filter],
  );
  return (
    <div className="picker">
      <ScanField
        id={id}
        label={t("stock.find.label")}
        disabled={disabled}
        host={host}
        onCode={(code) => {
          setWords("");
          lookup(code);
        }}
        onWords={(typed) => {
          reset();
          setWords(typed);
        }}
      />
      <LookupNotice state={state} missing={missing} />
      {words !== "" && matches.state.status === "error" ? (
        <p className="field__error" role="alert">
          {errorText(matches.state.error, t)}
        </p>
      ) : null}
      {words !== "" && matches.state.status === "ready" && matches.state.data !== null ? (
        matches.state.data.items.length === 0 ? (
          <p className="state">{t("stock.find.none")}</p>
        ) : (
          <ul className="picks" aria-label={t("stock.find.list")}>
            {matches.state.data.items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="pick"
                  disabled={disabled}
                  onClick={() => {
                    setWords("");
                    onPick(item);
                  }}
                >
                  <span>{item.name}</span>
                  <span>{item.tracked ? qtyWithUnit(item.onHand, item.unit) : item.unit}</span>
                </button>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </div>
  );
}
