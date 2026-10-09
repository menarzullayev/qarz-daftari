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

/**
 * `onDollars` is told, once the totals are read, whether the shop works in dollars, and `onAdvances`
 * whether it holds an advance of any customer (in either currency).
 */
function Totals({ onDollars, onAdvances }: { onDollars: (dollars: boolean) => void; onAdvances: (held: boolean) => void }) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const { state, reload } = useLoad((signal) => api.overview(signal), [api]);
  const dollars = state.status === "ready" && state.data.usd !== undefined;
  useEffect(() => onDollars(dollars), [onDollars, dollars]);
  const held = state.status === "ready" && (state.data.advances !== undefined || state.data.usd?.advances !== undefined);
  useEffect(() => onAdvances(held), [onAdvances, held]);

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
        {/* What the shop holds of customers who paid ahead: a figure of its own, never taken from the debt. */}
        {totals.advances ? (
          <div className="figure">
            <dt>
              <CheckIcon />
              {t("overview.advances")}
            </dt>
            <dd>{formatMoney(totals.advances.amount, language)}</dd>
            <dd className="figure__note">{formatCustomerCount(totals.advances.customers, language)}</dd>
          </div>
        ) : null}
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
          {usd.advances ? (
            <div className="figure">
              <dt>
                <CheckIcon />
                {t("overview.usd.advances")}
              </dt>
              <dd>{formatMoney(usd.advances.amount, language, "USD")}</dd>
              <dd className="figure__note">{formatCustomerCount(usd.advances.customers, language)}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}
    </>
  );
}

/**
 * `dollars`: the shop works in dollars, so the list can be asked for by the dollar debt too. `advances`:
 * the shop holds an advance, so the customers in credit can be listed instead; without one there is no
 * such choice and the section is the one it always was.
 */
function Debtors({ dollars, advances }: { dollars: boolean; advances: boolean }) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  const desktop = useDesktop();
  const [filter, setFilter] = useState<"all" | "overdue" | "inCredit">("all");
  const onlyOverdue = filter === "overdue";
  // The last advance may be used up while the list is open: the choice goes, and so does its list.
  const inCredit = advances && filter === "inCredit";
  const [chosen, setChosen] = useState<Currency>("UZS");
  // A shop without dollars has no such list: its request never names a currency.
  const currency: Currency = dollars ? chosen : "UZS";
  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) => api.debtors({ overdue: onlyOverdue, cursor, currency, ...(inCredit ? { inCredit } : {}) }, signal),
    [api, onlyOverdue, currency, inCredit],
  );

  let body: ReactNode;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.items.length === 0) {
    body = (
      <Empty icon={<CheckIcon />}>
        {inCredit ? t("overview.noInCredit") : onlyOverdue ? t("overview.noOverdue") : t("overview.noDebtors")}
      </Empty>
    );
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
        <button
          type="button"
          className="toggle__option"
          aria-pressed={!onlyOverdue && !inCredit}
          onClick={() => setFilter("all")}
        >
          {t("overview.filter.all")}
        </button>
        <button type="button" className="toggle__option" aria-pressed={onlyOverdue} onClick={() => setFilter("overdue")}>
          {t("overview.filter.overdue")}
        </button>
        {advances ? (
          <button type="button" className="toggle__option" aria-pressed={inCredit} onClick={() => setFilter("inCredit")}>
            {t("overview.filter.inCredit")}
          </button>
        ) : null}
      </div>
      {dollars ? <CurrencyToggle value={currency} onChange={setChosen} /> : null}
      {body}
    </section>
  );
}

/** Home of the workspace: what the shop is owed in total, and who owes it, largest debt first. */
export function OverviewScreen({ footer, extra }: { footer?: ReactNode; extra?: ReactNode }) {
  const [dollars, setDollars] = useState(false);
  const [advances, setAdvances] = useState(false);
  return (
    <>
      <SubscriptionBanner />
      <Totals onDollars={setDollars} onAdvances={setAdvances} />
      <Debtors dollars={dollars} advances={advances} />
      {footer}
      {extra}
    </>
  );
}
