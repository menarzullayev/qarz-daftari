import { useI18n } from "../../i18n/I18nProvider";
import type { PaymentHistory } from "../api";
import { formatMoney } from "../format";

/**
 * How a customer has paid this shop so far (REQ-045): the share of what fell due that was repaid by the
 * promised date, and the longest delay. It is the shop's own view of its own records and is for staff
 * only: the customer's page never shows it.
 */
export function PaymentHistoryNote({ history }: { history: PaymentHistory | null }) {
  const { t, language } = useI18n();
  return (
    <section aria-labelledby="history-title">
      <h2 id="history-title">{t("customer.history.title")}</h2>
      {history === null ? (
        <p className="state">{t("customer.history.none")}</p>
      ) : (
        <>
          <p>
            <span>{t("customer.history.onTime", { percent: history.onTimePercent })}</span>{" "}
            <span>
              {history.longestDelayDays > 0
                ? t("customer.history.longestDelay", { count: history.longestDelayDays })
                : t("customer.history.noDelay")}
            </span>
          </p>
          <p className="row__meta">
            {t("customer.history.amounts", {
              due: formatMoney(history.dueAmount, language),
              onTime: formatMoney(history.onTimeAmount, language),
            })}
          </p>
          <p className="hint">{t("customer.history.source")}</p>
        </>
      )}
    </section>
  );
}
