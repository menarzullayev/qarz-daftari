import { useState, type ReactNode } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { formatCustomerCount, formatMoney } from "../format";
import { useLoad, usePagedList } from "../hooks";
import { Link } from "../router";
import { useWorkspace } from "./context";
import { Empty, Failure, Loading, LoadMore, OverdueLines } from "./parts";
import { SubscriptionBanner } from "./SubscriptionScreen";

function Totals() {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const { state, reload } = useLoad((signal) => api.overview(signal), [api]);

  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return <Failure error={state.error} onRetry={reload} />;
  }
  const totals = state.data;
  return (
    <dl className="figures">
      <div className="figure figure--main">
        <dt>{t("overview.outstanding")}</dt>
        <dd>{formatMoney(totals.outstanding, language)}</dd>
        <dd className="figure__note">{formatCustomerCount(totals.debtors, language)}</dd>
      </div>
      <div className="figure">
        <dt>{t("overview.overdue")}</dt>
        <dd>{formatMoney(totals.overdueAmount, language)}</dd>
        <dd className="figure__note">{formatCustomerCount(totals.overdueCustomers, language)}</dd>
      </div>
      <div className="figure">
        <dt>{t("overview.dueToday")}</dt>
        <dd>{formatMoney(totals.dueToday, language)}</dd>
      </div>
    </dl>
  );
}

function Debtors() {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const [onlyOverdue, setOnlyOverdue] = useState(false);
  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) => api.debtors({ overdue: onlyOverdue, cursor }, signal),
    [api, onlyOverdue],
  );

  let body: ReactNode;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.items.length === 0) {
    body = <Empty>{onlyOverdue ? t("overview.noOverdue") : t("overview.noDebtors")}</Empty>;
  } else {
    body = (
      <>
        <ul className="rows">
          {state.items.map((debtor) => (
            <li key={debtor.id} className="row">
              <Link to={`/customers/${debtor.id}`} className="row__link">
                <span className="row__name">{debtor.displayName}</span>
                <span className="row__amount">{formatMoney(debtor.balance, language)}</span>
              </Link>
              <OverdueLines overdue={debtor.overdue} />
            </li>
          ))}
        </ul>
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
      {body}
    </section>
  );
}

/** Home of the workspace: what the shop is owed in total, and who owes it, largest debt first. */
export function OverviewScreen({ footer }: { footer?: ReactNode }) {
  return (
    <>
      <SubscriptionBanner />
      <Totals />
      <Debtors />
      {footer}
    </>
  );
}
