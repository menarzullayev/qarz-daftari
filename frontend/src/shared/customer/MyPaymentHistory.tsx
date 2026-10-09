import { useI18n } from "../../i18n/I18nProvider";
import type { PaymentHistory } from "../api";
import { formatMoney } from "../format";
import type { Currency } from "../money";

/**
 * The customer's own payment history in this shop (BR-9), shown to the customer on their own page since
 * the founder's decision of 2026-10-08 (DEC-066): the share of what fell due that they repaid by the
 * promised date, and their longest delay. The figures are the ones the shop's staff see; the words are
 * addressed to the customer about themselves. The seller's note and the author of an entry stay the
 * shop's own and are not shown here.
 */
function Figures({ history, currency }: { history: PaymentHistory | null; currency?: Currency }) {
  const { t, language } = useI18n();
  return history === null ? (
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
          due: formatMoney(history.dueAmount, language, currency),
          onTime: formatMoney(history.onTimeAmount, language, currency),
        })}
      </p>
    </>
  );
}

/**
 * `usd` is the history of the dollar debt, counted apart from the so'm one: null while no dollar debt
 * has fallen due, and absent for a shop without dollars, where nothing more is shown.
 */
export function MyPaymentHistory({ history, usd }: { history: PaymentHistory | null; usd?: PaymentHistory | null }) {
  const { t } = useI18n();
  return (
    <section aria-labelledby="my-history-title">
      <h2 id="my-history-title">{t("my.history.title")}</h2>
      <Figures history={history} />
      {usd === undefined ? null : (
        <div role="group" aria-labelledby="my-history-usd-title">
          <h3 id="my-history-usd-title">{t("currency.usd.title")}</h3>
          <Figures history={usd} currency="USD" />
        </div>
      )}
      <p className="hint">{t("my.history.source")}</p>
    </section>
  );
}
