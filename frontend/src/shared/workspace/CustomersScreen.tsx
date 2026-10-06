import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { Customer } from "../api";
import { formatMoney } from "../format";
import { usePagedList } from "../hooks";
import { Link } from "../router";
import { useWorkspace } from "./context";
import { Empty, Failure, Loading, LoadMore } from "./parts";

/** How long typing must pause before the list is searched again. */
export const SEARCH_DELAY_MS = 300;
const MAX_QUERY_LENGTH = 80;

type Status = "active" | "archived";

type CustomersScreenProps = {
  /**
   * False for the customer book, where a row opens the customer. True for the first step of a new
   * entry, where a row offers the two things a seller records: a credit sale and a payment.
   */
  pick?: boolean;
};

function Row({ customer, pick }: { customer: Customer; pick: boolean }) {
  const { t, language } = useI18n();
  if (!pick) {
    return (
      <li className="row">
        <Link to={`/customers/${customer.id}`} className="row__link">
          <span className="row__name">{customer.displayName}</span>
          <span className="row__amount">{formatMoney(customer.balance, language)}</span>
        </Link>
        {customer.phone ? <p className="row__meta">{customer.phone}</p> : null}
      </li>
    );
  }
  return (
    <li className="row">
      <p className="row__link">
        <span className="row__name">{customer.displayName}</span>
        <span className="row__amount">{formatMoney(customer.balance, language)}</span>
      </p>
      <p className="actions">
        <Link to={`/customers/${customer.id}/credit`} className="button button--primary">
          {t("entry.credit.short")}
        </Link>
        {/* A payment cannot exceed the debt, so there is nothing to pay when nothing is owed. */}
        {customer.balance > 0 ? (
          <Link to={`/customers/${customer.id}/payment`} className="button">
            {t("entry.payment.short")}
          </Link>
        ) : null}
      </p>
    </li>
  );
}

/** The customer book with search by name or phone, used both for browsing and for picking a customer. */
export function CustomersScreen({ pick = false }: CustomersScreenProps) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<Status>("active");

  // Search as the seller types, once typing pauses; submitting the form searches at once.
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(text.trim()), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [text]);

  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) => api.listCustomers({ q: query, status, cursor }, signal),
    [api, query, status],
  );

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setQuery(text.trim());
  };

  let body: ReactNode;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.items.length === 0) {
    body = (
      <Empty>
        {query !== "" ? t("customers.noMatch") : status === "archived" ? t("customers.noArchived") : t("customers.none")}
      </Empty>
    );
  } else {
    body = (
      <>
        <ul className="rows">
          {state.items.map((customer) => (
            <Row key={customer.id} customer={customer} pick={pick} />
          ))}
        </ul>
        {state.nextCursor !== null ? (
          <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} />
        ) : null}
      </>
    );
  }

  return (
    <>
      {pick ? <p className="hint">{t("entry.pickCustomer")}</p> : null}
      <form className="search" role="search" onSubmit={onSubmit}>
        <input
          type="search"
          className="input"
          value={text}
          maxLength={MAX_QUERY_LENGTH}
          aria-label={t("customers.search")}
          placeholder={t("customers.search")}
          onChange={(event) => setText(event.target.value)}
        />
        <button type="submit" className="button">
          {t("action.search")}
        </button>
      </form>
      <div className="bar">
        {pick ? null : (
          <div className="toggle" role="group" aria-label={t("customers.status")}>
            <button
              type="button"
              className="toggle__option"
              aria-pressed={status === "active"}
              onClick={() => setStatus("active")}
            >
              {t("customers.status.active")}
            </button>
            <button
              type="button"
              className="toggle__option"
              aria-pressed={status === "archived"}
              onClick={() => setStatus("archived")}
            >
              {t("customers.status.archived")}
            </button>
          </div>
        )}
        <Link to="/customers/new" className="button">
          {t("customers.add")}
        </Link>
      </div>
      {body}
      {pick ? null : (
        <nav className="actions" aria-label={t("link.nav")}>
          <Link to="/customers/waiting" className="button">
            {t("waiting.title")}
          </Link>
          <Link to="/customers/counter-code" className="button">
            {t("counter.title")}
          </Link>
        </nav>
      )}
    </>
  );
}
