import { useI18n } from "../../i18n/I18nProvider";
import type { PaymentHistory } from "../api";
import { formatMoney } from "../format";
import type { Currency } from "../money";

/**
 * How a customer has paid this shop so far (REQ-045): the share of what fell due that was repaid by the
 * promised date, and the longest delay. It is the shop's own view of its own records and is for staff
 * only: the customer's page never shows it.
 */
function Figures({ history, currency }: { history: PaymentHistory; currency?: Currency }) {
  const { t, language } = useI18n();
  return (
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
          due: formatMoney(history.dueAmount, language, currency),
          onTime: formatMoney(history.onTimeAmount, language, currency),
        })}
      </p>
    </>
  );
}

/**
 * `usd` is the history of the dollar debt, which is counted apart from the so'm one: null while no
 * dollar debt has fallen due, and absent in a shop without dollars, where nothing more is shown.
 */
export function PaymentHistoryNote({ history, usd }: { history: PaymentHistory | null; usd?: PaymentHistory | null }) {
  const { t } = useI18n();
  return (
    <section aria-labelledby="history-title">
      <h2 id="history-title">{t("customer.history.title")}</h2>
      {history === null ? (
        <p className="state">{t("customer.history.none")}</p>
      ) : (
        <>
          <Figures history={history} />
          <p className="hint">{t("customer.history.source")}</p>
        </>
      )}
      {usd === undefined ? null : (
        <div role="group" aria-labelledby="history-usd-title">
          <h3 id="history-usd-title">{t("currency.usd.title")}</h3>
          {usd === null ? <p className="state">{t("customer.history.none")}</p> : <Figures history={usd} currency="USD" />}
        </div>
      )}
    </section>
  );
}
