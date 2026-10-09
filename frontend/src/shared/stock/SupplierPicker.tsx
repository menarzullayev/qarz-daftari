import { useMemo } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { OptionPicker, type PickLoader, type PickOption } from "./OptionPicker";
import { useStock } from "./parts";
import type { StockApi } from "./stockApi";

/** How many suppliers one page of the choice asks for (the server allows up to 100). */
export const SUPPLIER_PAGE = 20;

type SupplierStatus = "active" | "archived";

/**
 * Reads the shop's suppliers for a choice, a page at a time, by a part of the name: every one of them
 * can be reached, however many there are (`GET suppliers?q=&status=&cursor=&limit=`).
 *
 * The server lists one status at a time, so the statuses asked for follow each other: the working
 * suppliers first, then (where asked for) the archived ones, marked as such. A list that ends gives
 * way to the next in the same page, so a few working suppliers do not hide the archived ones behind
 * "more". The cursor says which list the server's own cursor belongs to.
 */
export function supplierLoader(stock: StockApi, statuses: readonly SupplierStatus[], archivedWord: string): PickLoader {
  return async (query, cursor, signal) => {
    const cut = cursor === null ? -1 : cursor.indexOf(":");
    let index = cursor === null ? 0 : Number(cursor.slice(0, cut));
    let after = cursor === null || cut === cursor.length - 1 ? null : cursor.slice(cut + 1);
    const options: PickOption[] = [];
    for (;;) {
      const status = statuses[index];
      if (status === undefined) {
        return { options, nextCursor: null };
      }
      const page = await stock.suppliers({ q: query, status, cursor: after, limit: SUPPLIER_PAGE }, signal);
      const detail = status === "archived" ? archivedWord : undefined;
      options.push(...page.suppliers.map((supplier) => ({ id: supplier.id, name: supplier.name, detail })));
      if (page.nextCursor !== null) {
        return { options, nextCursor: `${index}:${page.nextCursor}` };
      }
      index += 1;
      after = null;
      if (options.length >= SUPPLIER_PAGE) {
        return { options, nextCursor: index < statuses.length ? `${index}:` : null };
      }
    }
  };
}

const WORKING: readonly SupplierStatus[] = ["active"];
const EVERY: readonly SupplierStatus[] = ["active", "archived"];

/**
 * The choice of a supplier, out of all of them. Drawn by the caller only for a member who may read the
 * suppliers ("suppliers.view"): nothing here asks whether they may.
 */
export function SupplierPicker({
  id,
  value,
  onChange,
  archived = false,
  noneLabel,
  placeholder,
  disabled,
  invalid,
  describedBy,
}: {
  id: string;
  value: PickOption | null;
  onChange: (supplier: PickOption | null) => void;
  /** Offer the archived suppliers after the working ones: their documents are still in the books. */
  archived?: boolean;
  noneLabel?: string | undefined;
  placeholder?: string | undefined;
  disabled?: boolean | undefined;
  invalid?: boolean | undefined;
  describedBy?: string | undefined;
}) {
  const { t } = useI18n();
  const stock = useStock();
  const archivedWord = t("supplier.status.archived");
  const load = useMemo(() => supplierLoader(stock, archived ? EVERY : WORKING, archivedWord), [stock, archived, archivedWord]);
  return (
    <OptionPicker
      id={id}
      label={t("stock.doc.supplier")}
      value={value}
      load={load}
      onChange={onChange}
      noneLabel={noneLabel}
      placeholder={placeholder}
      disabled={disabled}
      invalid={invalid}
      describedBy={describedBy}
    />
  );
}
