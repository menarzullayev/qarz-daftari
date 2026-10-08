import { useI18n } from "../../i18n/I18nProvider";
import type { PaymentHistory } from "../api";
import { formatMoney } from "../format";

/**
 * The customer's own payment history in this shop (BR-9), shown to the customer on their own page since
 * the founder's decision of 2026-10-08 (DEC-066): the share of what fell due that they repaid by the
 * promised date, and their longest delay. The figures are the ones the shop's staff see; the words are
 * addressed to the customer about themselves. The seller's note and the author of an entry stay the
 * shop's own and are not shown here.
 */
export function MyPaymentHistory({ history }: { history: PaymentHistory | null }) {
  const { t, language } = useI18n();
  return (
    <section aria-labelledby="my-history-title">
      <h2 id="my-history-title">{t("my.history.title")}</h2>
      {history === null ? (
        <p className="state">{t("my.history.none")}</p>
      ) : (
        <>
          <p>
            <span>{t("my.history.onTime", { percent: history.onTimePercent })}</span>{" "}
            <span>
              {history.longestDelayDays > 0
                ? t("my.history.longestDelay", { count: history.longestDelayDays })
                : t("my.history.noDelay")}
            </span>
          </p>
          <p className="row__meta">
            {t("my.history.amounts", {
              due: formatMoney(history.dueAmount, language),
              onTime: formatMoney(history.onTimeAmount, language),
            })}
          </p>
        </>
      )}
      <p className="hint">{t("my.history.source")}</p>
    </section>
  );
}
