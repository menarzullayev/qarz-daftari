import { useI18n } from "../../i18n/I18nProvider";
import type { MessageKey } from "../../i18n/types";
import type { Column } from "../layout";
import { Figures } from "../reports/Figures";
import { money } from "./amounts";
import type { CashCurrency, CashLine, CashTotal } from "./cashApi";

/**
 * What each way of paying opened with, took in, paid out and closed with, one table for each currency.
 * The two currencies are never in one table and never in one sum: each has its own total.
 */
export function Balances({
  lines,
  totals,
  labelKey,
}: {
  lines: readonly CashLine[];
  totals: readonly CashTotal[];
  /** How the first column is named: a day's opening balance, or a period's. */
  labelKey: Extract<MessageKey, "cash.balances.opening" | "cash.balances.openingPeriod">;
}) {
  const { t, language } = useI18n();
  const several = totals.length > 1;
  const table = (total: CashTotal) => {
    const currency: CashCurrency = total.currency;
    const show = (amount: number) => money(amount, currency, language);
    const caption = several ? t("cash.balances.of", { currency: t(`cash.currency.${currency}`) }) : t("cash.balances");
    const detail = (line: CashTotal) =>
      t("cash.balances.row", { opening: show(line.opening), income: show(line.income), expense: show(line.expense) });
    const columns: Column<CashLine>[] = [
      { id: "method", header: t("cash.balances.method"), rowHeader: true, cell: (line) => t(`cash.method.${line.method}`) },
      { id: "opening", header: t(labelKey), numeric: true, cell: (line) => show(line.opening) },
      { id: "income", header: t("cash.balances.income"), numeric: true, cell: (line) => show(line.income) },
      { id: "expense", header: t("cash.balances.expense"), numeric: true, cell: (line) => show(line.expense) },
      { id: "closing", header: t("cash.balances.closing"), numeric: true, cell: (line) => show(line.closing) },
    ];
    return (
      <section key={currency} aria-labelledby={`cash-balances-${currency}`}>
        <h3 id={`cash-balances-${currency}`}>{caption}</h3>
        <Figures
          caption={caption}
          columns={columns}
          items={lines.filter((line) => line.currency === currency)}
          rowKey={(line) => line.method}
          row={(line) => ({ name: t(`cash.method.${line.method}`), amount: show(line.closing), detail: detail(line) })}
          foot={[
            t("cash.balances.total"),
            show(total.opening),
            show(total.income),
            show(total.expense),
            show(total.closing),
          ]}
          footRow={{ name: t("cash.balances.total"), amount: show(total.closing), detail: detail(total) }}
        />
      </section>
    );
  };
  return (
    <>
      {totals.map(table)}
      {several ? <p className="hint">{t("cash.balances.separate")}</p> : null}
    </>
  );
}
