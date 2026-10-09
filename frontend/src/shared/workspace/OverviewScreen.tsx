import { useEffect, useState, type ReactNode } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { formatCustomerCount, formatMoney } from "../format";
import { useLoad, usePagedList } from "../hooks";
import { AlertIcon, CardIcon, CheckIcon, ClockIcon } from "../icons";
import { useDesktop } from "../layout";
import type { Currency } from "../money";
import { Link } from "../router";
import { useWorkspace } from "./context";
import { Avatar, CurrencyToggle, Empty, Failure, Loading, LoadMore, Money, OverdueLines } from "./parts";
import { SubscriptionBanner } from "./SubscriptionScreen";

/** `onDollars` is told, once the totals are read, whether the shop works in dollars. */
function Totals({ onDollars }: { onDollars: (dollars: boolean) => void }) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const { state, reload } = useLoad((signal) => api.overview(signal), [api]);
  const dollars = state.status === "ready" && state.data.usd !== undefined;
  useEffect(() => onDollars(dollars), [onDollars, dollars]);

  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return <Failure error={state.error} onRetry={reload} />;
  }
  const totals = state.data;
  const usd = totals.usd;
  return (
    <>
      <dl className="figures">
        <div className="figure figure--main">
          <dt>
            <CardIcon />
            {t("overview.outstanding")}
          </dt>
          <dd>{formatMoney(totals.outstanding, language)}</dd>
          <dd className="figure__note">{formatCustomerCount(totals.debtors, language)}</dd>
        </div>
        <div className="figure">
          <dt>
            <AlertIcon />
            {t("overview.overdue")}
          </dt>
          <dd>{formatMoney(totals.overdueAmount, language)}</dd>
          <dd className="figure__note">{formatCustomerCount(totals.overdueCustomers, language)}</dd>
        </div>
        <div className="figure">
          <dt>
            <ClockIcon />
            {t("overview.dueToday")}
          </dt>
          <dd>{formatMoney(totals.dueToday, language)}</dd>
        </div>
      </dl>
      {/* The dollar debts are a book of their own: the same three figures, never added to the so'm ones. */}
      {usd ? (
        <dl className="figures">
          <div className="figure">
            <dt>
              <CardIcon />
              {t("overview.usd.outstanding")}
            </dt>
            <dd>{formatMoney(usd.outstanding, language, "USD")}</dd>
            <dd className="figure__note">{formatCustomerCount(usd.debtors, language)}</dd>
          </div>
          <div className="figure">
            <dt>
              <AlertIcon />
              {t("overview.usd.overdue")}
            </dt>
            <dd>{formatMoney(usd.overdueAmount, language, "USD")}</dd>
            <dd className="figure__note">{formatCustomerCount(usd.overdueCustomers, language)}</dd>
          </div>
          <div className="figure">
            <dt>
              <ClockIcon />
              {t("overview.usd.dueToday")}
            </dt>
            <dd>{formatMoney(usd.dueToday, language, "USD")}</dd>
          </div>
        </dl>
      ) : null}
    </>
  );
}

/** `dollars`: the shop works in dollars, so the list can be asked for by the dollar debt too. */
function Debtors({ dollars }: { dollars: boolean }) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  const desktop = useDesktop();
  const [onlyOverdue, setOnlyOverdue] = useState(false);
  const [chosen, setChosen] = useState<Currency>("UZS");
  // A shop without dollars has no such list: its request never names a currency.
  const currency: Currency = dollars ? chosen : "UZS";
  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) => api.debtors({ overdue: onlyOverdue, cursor, currency }, signal),
    [api, onlyOverdue, currency],
  );

  let body: ReactNode;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.items.length === 0) {
    body = <Empty icon={<CheckIcon />}>{onlyOverdue ? t("overview.noOverdue") : t("overview.noDebtors")}</Empty>;
  } else {
    body = (
      <>
        {desktop ? (
          <desktop.DebtorsTable items={state.items} />
        ) : (
          <ul className="rows">
            {state.items.map((debtor) => (
              <li key={debtor.id} className="row row--person">
                <Link to={`/customers/${debtor.id}`} className="row__link">
                  <Avatar name={debtor.displayName} />
                  <span className="row__name">{debtor.displayName}</span>
                  <span className="row__amount">
                    <Money uzs={debtor.balance} usd={debtor.usd?.balance} />
                  </span>
                </Link>
                <OverdueLines overdue={debtor.overdue} />
                {debtor.usd?.overdue ? <OverdueLines overdue={debtor.usd.overdue} currency="USD" /> : null}
              </li>
            ))}
          </ul>
        )}
        {state.nextCursor !== null ? (
          <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} />
        ) : null}
      </>
    );
  }

  return (
    <section aria-labelledby="debtors-title">
      <h2 id="debtors-title">{t("overview.debtors")}</h2>
      <div className="toggle" role="group" aria-label={t("overview.filter")}>
        <button type="button" className="toggle__option" aria-pressed={!onlyOverdue} onClick={() => setOnlyOverdue(false)}>
          {t("overview.filter.all")}
        </button>
        <button type="button" className="toggle__option" aria-pressed={onlyOverdue} onClick={() => setOnlyOverdue(true)}>
          {t("overview.filter.overdue")}
        </button>
      </div>
      {dollars ? <CurrencyToggle value={currency} onChange={setChosen} /> : null}
      {body}
    </section>
  );
}

/** Home of the workspace: what the shop is owed in total, and who owes it, largest debt first. */
export function OverviewScreen({ footer, extra }: { footer?: ReactNode; extra?: ReactNode }) {
  const [dollars, setDollars] = useState(false);
  return (
    <>
      <SubscriptionBanner />
      <Totals onDollars={setDollars} />
      <Debtors dollars={dollars} />
      {footer}
      {extra}
    </>
  );
}
